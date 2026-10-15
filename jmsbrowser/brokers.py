"""Provider adapters. Message reads only use JMS QueueBrowser enumeration."""
from __future__ import annotations

from dataclasses import asdict
import base64
import fnmatch
import json
import ssl
import urllib.request
from urllib.parse import urlsplit

from .config import ConnectionConfig
from .domain import Consumer, ConsumerSnapshot, Message, QueueInfo
from .runtime import start_jvm
from .outgoing import OutgoingMessage, SendFailure


def java_value(value, limit=4096):
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    name = str(value.getClass().getName()) if hasattr(value, "getClass") else ""
    if name == "java.lang.Boolean":
        return bool(value.booleanValue())
    if name in ("java.lang.Byte", "java.lang.Short", "java.lang.Integer", "java.lang.Long"):
        return int(value.longValue())
    if name in ("java.lang.Float", "java.lang.Double"):
        return float(value.doubleValue())
    if name == "[B":
        size = len(value)
        return {"type": "bytes", "size": size, "hex": bytes(int(v) & 255 for v in value[:limit]).hex(), "truncated": size > limit}
    return str(value)


def bounded_text(text, limit):
    encoded = text.encode("utf-8")
    return encoded[:limit].decode("utf-8", errors="ignore"), len(encoded), len(encoded) > limit


def extract_message(message, limit):
    import jpype
    J = jpype.JClass
    headers = {}
    for key in ("JMSMessageID", "JMSType", "JMSPriority", "JMSTimestamp", "JMSExpiration", "JMSDeliveryMode", "JMSRedelivered", "JMSCorrelationID", "JMSReplyTo", "JMSDestination"):
        headers[key] = java_value(getattr(message, "get" + key)())
    properties = {}
    names = message.getPropertyNames()
    while names.hasMoreElements():
        name = str(names.nextElement())
        properties[name] = java_value(message.getObjectProperty(name))
    truncated, size = False, None
    note = "Payload bytes; text measured as UTF-8. Excludes headers."
    if isinstance(message, J("javax.jms.TextMessage")):
        kind = "TextMessage"
        body, size, truncated = bounded_text(str(message.getText() or ""), limit)
    elif isinstance(message, J("javax.jms.BytesMessage")):
        kind = "BytesMessage"
        size = int(message.getBodyLength())
        message.reset()
        raw = bytearray()
        while len(raw) < min(size, limit):
            buffer = jpype.JArray(jpype.JByte)(min(8192, min(size, limit) - len(raw)))
            count = int(message.readBytes(buffer))
            if count <= 0:
                break
            raw.extend(int(value) & 255 for value in buffer[:count])
        body = "\n".join(f"{offset:08x}  {raw[offset:offset + 16].hex(' ')}" for offset in range(0, len(raw), 16))
        truncated = size > len(raw)
    elif isinstance(message, J("javax.jms.MapMessage")):
        kind, values = "MapMessage", {}
        names = message.getMapNames()
        used = 0
        while names.hasMoreElements() and used < limit:
            name = str(names.nextElement())
            value = java_value(message.getObject(name), limit=min(limit, 4096))
            value = value[:limit] if isinstance(value, str) else value
            values[name] = value
            used += len(json.dumps({name: value}, ensure_ascii=False).encode("utf-8"))
        text = json.dumps(values, indent=2, ensure_ascii=False)
        body, _, clipped = bounded_text(text, limit)
        truncated = clipped or bool(names.hasMoreElements())
        note = "n/a: JMS does not expose the encoded MapMessage payload size."
    elif isinstance(message, J("javax.jms.StreamMessage")):
        kind, values, used = "StreamMessage", [], 0
        message.reset()
        while used < limit:
            try:
                value = java_value(message.readObject(), limit=min(limit, 4096))
            except J("javax.jms.MessageEOFException"):
                break
            value = value[:limit] if isinstance(value, str) else value
            values.append(value)
            used += len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        text = json.dumps(values, indent=2, ensure_ascii=False)
        body, _, clipped = bounded_text(text, limit)
        truncated = clipped or used >= limit
        note = "n/a: JMS does not expose the encoded StreamMessage payload size."
    elif isinstance(message, J("javax.jms.ObjectMessage")):
        kind, body = "ObjectMessage", "Serialized object body is not deserialized."
        note = "n/a: JMS does not expose the serialized ObjectMessage size."
    else:
        kind, body, size = "Message", "", 0
    return Message(str(headers["JMSMessageID"] or ""), kind, int(headers["JMSPriority"]), body, size, headers, properties, truncated, note)


class JMSAdapter:
    def __init__(self, config: ConnectionConfig):
        self.config = config
        self.connection = self.session = self.factory = None

    def connect(self):
        import jpype
        start_jvm(self.config.type, self.config.tls)
        J = jpype.JClass
        cfg = self.config
        if cfg.type == "artemis":
            host = f"[{cfg.host}]" if ":" in cfg.host else cfg.host
            options = f"callTimeout={cfg.timeout_seconds * 1000}&initialConnectAttempts=1&reconnectAttempts=0"
            if cfg.tls.get("enabled"):
                options += "&sslEnabled=true&useDefaultSslContext=true&verifyHost=true"
            self.factory = J("org.apache.activemq.artemis.jms.client.ActiveMQConnectionFactory")(f"tcp://{host}:{cfg.port}?{options}")
        else:
            self.factory = J("com.ibm.mq.jms.MQConnectionFactory")()
            self.factory.setHostName(cfg.host)
            self.factory.setPort(cfg.port)
            self.factory.setQueueManager(cfg.queue_manager)
            self.factory.setChannel(cfg.channel)
            self.factory.setTransportType(1)  # WMQ_CM_CLIENT
            constants = J("com.ibm.msg.client.wmq.WMQConstants")
            self.factory.setBooleanProperty(constants.USER_AUTHENTICATION_MQCSP, True)
            if cfg.tls.get("enabled"):
                self.factory.setSSLCipherSuite(cfg.tls["cipher_suite"])
        try:
            self.connection = self.factory.createConnection(cfg.username, cfg.password())
            if hasattr(self, "on_error"):
                self.exception_listener = jpype.JProxy("javax.jms.ExceptionListener", dict(onException=self.on_error))
                self.connection.setExceptionListener(self.exception_listener)
            self.session = self.connection.createSession(False, 1)  # AUTO_ACKNOWLEDGE; QueueBrowser never receives/acks.
            self.connection.start()
        except Exception:
            self.close()
            raise

    def browse(self, queue_name, limit=100, max_body_bytes=262144):
        queue = self.session.createQueue(queue_name)
        browser = self.session.createBrowser(queue)
        try:
            enumeration = browser.getEnumeration()
            messages = []
            while len(messages) < limit and enumeration.hasMoreElements():
                messages.append(extract_message(enumeration.nextElement(), max_body_bytes))
            return messages
        finally:
            browser.close()

    def send_message(self, queue_name, draft: OutgoingMessage):
        import jpype
        producer, attempted = None, False
        try:
            properties = draft.validate()
            message = self.session.createTextMessage(draft.body)
            if draft.jms_type:
                message.setJMSType(draft.jms_type)
            if draft.correlation_id:
                message.setJMSCorrelationID(draft.correlation_id)
            for name, kind, value in properties:
                converted = value if kind in ("String", "Boolean") else getattr(jpype, "J" + kind)(value)
                getattr(message, "set" + kind + "Property")(name, converted)
            producer = self.session.createProducer(self.session.createQueue(queue_name))
            attempted = True
            producer.send(message, 2 if draft.persistent else 1, draft.priority, jpype.JLong(draft.ttl_ms))
        except Exception as error:
            raise SendFailure(str(error), "unknown" if attempted else "not_sent") from error
        finally:
            if producer is not None:
                try:
                    producer.close()
                except Exception:
                    pass  # Closing a producer does not undo a completed send.
        receipt = {"queue": queue_name, "size": len(draft.body.encode("utf-8")), "demo": False}
        for key in ("JMSMessageID", "JMSTimestamp", "JMSExpiration"):
            try:
                receipt[key] = java_value(getattr(message, "get" + key)())
            except Exception:
                receipt[key] = None
        return receipt

    def close(self):
        for resource in (self.session, self.connection, self.factory):
            if resource is not None and hasattr(resource, "close"):
                try:
                    resource.close()
                except Exception:
                    pass
        self.session = self.connection = self.factory = None


class ArtemisAdapter(JMSAdapter):
    def jolokia(self, payload):
        cfg = self.config
        parsed = urlsplit(cfg.management_url)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password:
            raise ValueError("management_url must be an HTTP(S) URL without credentials.")
        credentials = f"{cfg.management_username or cfg.username}:{cfg.password(management=True)}"
        headers = {"Content-Type": "application/json", "Authorization": "Basic " + base64.b64encode(credentials.encode()).decode()}
        request = urllib.request.Request(cfg.management_url.rstrip("/"), json.dumps(payload).encode(), headers, method="POST")
        options = {"timeout": cfg.timeout_seconds}
        if parsed.scheme == "https":
            if not hasattr(self, "management_ssl_context"):
                self.management_ssl_context = ssl.create_default_context(cafile=cfg.management_ca_file or None)
            options["context"] = self.management_ssl_context
        with urllib.request.urlopen(request, **options) as response:
            data = json.load(response)
        for item in data if isinstance(data, list) else [data]:
            if item.get("status") != 200:
                raise RuntimeError(f"Artemis management request failed: {item.get('error', item.get('status'))}")
        return data

    def list_queues(self):
        import jpype
        ObjectName = jpype.JClass("javax.management.ObjectName")
        pattern = "org.apache.activemq.artemis:broker=*,component=addresses,address=*,subcomponent=queues,routing-type=*,queue=*"
        names = self.jolokia({"type": "search", "mbean": pattern})["value"]
        queues = []
        for mbean in names:
            obj = ObjectName(mbean)
            name = str(obj.getKeyProperty("queue"))
            if name.startswith('"'):
                name = str(ObjectName.unquote(name))
            if fnmatch.fnmatchcase(name, self.config.queue_pattern) and (not self.config.queues or name in self.config.queues):
                routing = str(obj.getKeyProperty("routing-type"))
                if routing.startswith('"'):
                    routing = str(ObjectName.unquote(routing))
                queues.append((name, mbean, routing.upper()))
        self.queue_mbeans = {name: mbean for name, mbean, _ in queues}
        if not queues:
            return []
        depths = self.jolokia([{"type": "read", "mbean": mbean, "attribute": "MessageCount"} for _, mbean, _ in queues])
        return sorted([QueueInfo(name, int(depth["value"]), routing) for (name, _, routing), depth in zip(queues, depths)], key=lambda q: q.name)

    def queue_depth(self, queue_name):
        mbean = self.queue_mbeans[queue_name]
        return int(self.jolokia({"type": "read", "mbean": mbean, "attribute": "MessageCount"})["value"])

    def consumers(self, queue_name):
        import jpype
        mbean = self.queue_mbeans[queue_name]
        raw = self.jolokia({"type": "exec", "mbean": mbean, "operation": "listConsumersAsJSON()", "arguments": []})["value"]
        # The queue snapshot is authoritative; the richer server view omits browseOnly.
        attached = [item for item in json.loads(raw) if str(item.get("browseOnly", False)).lower() != "true"]
        if not attached:
            return ConsumerSnapshot(queue_name)
        broker = str(jpype.JClass("javax.management.ObjectName")(mbean).getKeyProperty("broker"))
        enriched, note = {}, ""
        try:
            result = self.jolokia({"type": "exec", "mbean": "org.apache.activemq.artemis:broker=" + broker,
                                   "operation": "listConsumers(java.lang.String,int,int)",
                                   "arguments": [json.dumps({"field": "queue", "operation": "EQUALS", "value": queue_name}), -1, -1]})
            enriched = {str(item["id"]): item for item in json.loads(result["value"])["data"] if item.get("queue") == queue_name}
        except Exception as error:
            note = f"Connection details unavailable: {error}"
        consumers = []
        for item in attached:
            identity = str(item.get("sequentialId", ""))
            extra = enriched.get(identity, {})
            consumers.append(Consumer(consumer_id=identity or str(item.get("consumerID", "")),
                                      application=str(extra.get("clientID") or ""), remote_address=str(extra.get("remoteAddress") or ""),
                                      user=str(extra.get("validatedUser") or extra.get("user") or ""), protocol=str(extra.get("protocol") or ""),
                                      selector=extra.get("filter"), connection_id=str(item.get("connectionID") or ""),
                                      session_id=str(item.get("sessionID") or ""), status=str(item.get("status") or ""),
                                      details={"Session consumer ID": item.get("consumerID"), "Created": extra.get("creationTime", "n/a")}))
        return ConsumerSnapshot(queue_name, sorted(consumers, key=lambda item: item.consumer_id), note=note)


class IBMAdapter(JMSAdapter):
    def inquiry_agent(self):
        agent = getattr(self, "pcf_agent", None)
        if agent is None:
            import jpype
            agent = jpype.JClass("com.ibm.mq.headers.pcf.PCFMessageAgent")()
            agent.setModelQueueName(self.config.model_queue)
            agent.setReplyQueuePrefix(self.config.reply_prefix)
            agent.setWaitInterval(self.config.timeout_seconds)
            agent.connect(self.queue_manager_connection())
            self.pcf_agent = agent
        return agent

    def queue_manager_connection(self):
        manager = getattr(self, "inquiry_manager", None)
        if manager is not None and manager.isConnected():
            return manager
        import jpype
        J = jpype.JClass
        C = J("com.ibm.mq.constants.CMQC")
        properties = J("java.util.Hashtable")()
        for key, value in ((C.HOST_NAME_PROPERTY, self.config.host), (C.CHANNEL_PROPERTY, self.config.channel), (C.USER_ID_PROPERTY, self.config.username), (C.PASSWORD_PROPERTY, self.config.password())):
            properties.put(key, value)
        properties.put(C.PORT_PROPERTY, J("java.lang.Integer")(self.config.port))
        properties.put(C.TRANSPORT_PROPERTY, C.TRANSPORT_MQSERIES_CLIENT)
        properties.put(C.USE_MQCSP_AUTHENTICATION_PROPERTY, J("java.lang.Boolean")(True))
        if self.config.tls.get("enabled"):
            properties.put(C.SSL_CIPHER_SUITE_PROPERTY, self.config.tls["cipher_suite"])
        self.inquiry_manager = J("com.ibm.mq.MQQueueManager")(self.config.queue_manager, properties)
        return self.inquiry_manager

    def queue_depth(self, queue_name):
        import jpype
        C = jpype.JClass("com.ibm.mq.constants.CMQC")
        manager = self.queue_manager_connection()
        handle = manager.accessQueue(queue_name, C.MQOO_INQUIRE | C.MQOO_FAIL_IF_QUIESCING)
        try:
            return int(handle.getCurrentDepth())
        finally:
            handle.close()

    def list_queues(self):
        import jpype
        J = jpype.JClass
        C = J("com.ibm.mq.constants.CMQC")
        F = J("com.ibm.mq.constants.CMQCFC")
        manager = self.queue_manager_connection()
        if self.config.queues:
            queues = []
            for name in self.config.queues:
                if not fnmatch.fnmatchcase(name, self.config.queue_pattern):
                    continue
                handle = manager.accessQueue(name, C.MQOO_INQUIRE | C.MQOO_FAIL_IF_QUIESCING)
                try:
                    queues.append(QueueInfo(name, int(handle.getCurrentDepth()), "LOCAL"))
                finally:
                    handle.close()
            return sorted(queues, key=lambda q: q.name)
        agent = self.inquiry_agent()
        reply_name = str(agent.getReplyQueueName()).strip()
        request = J("com.ibm.mq.headers.pcf.PCFMessage")(F.MQCMD_INQUIRE_Q)
        request.addParameter(C.MQCA_Q_NAME, self.config.queue_pattern)
        request.addParameter(C.MQIA_Q_TYPE, C.MQQT_LOCAL)
        request.addParameter(F.MQIACF_Q_ATTRS, jpype.JArray(jpype.JInt)([C.MQCA_Q_NAME, C.MQIA_CURRENT_Q_DEPTH]))
        queues = []
        for response in agent.send(request):
            name = str(response.getStringParameterValue(C.MQCA_Q_NAME)).strip()
            if name != reply_name and not fnmatch.fnmatchcase(name, self.config.reply_prefix) and fnmatch.fnmatchcase(name, self.config.queue_pattern):
                queues.append(QueueInfo(name, int(response.getIntParameterValue(C.MQIA_CURRENT_Q_DEPTH)), "LOCAL"))
        return sorted(queues, key=lambda q: q.name)

    def consumers(self, queue_name):
        import jpype
        J = jpype.JClass
        C, F = J("com.ibm.mq.constants.CMQC"), J("com.ibm.mq.constants.CMQCFC")
        note = "Input handles may share an application/process. Selectors and unique handle IDs are not exposed."
        if fnmatch.fnmatchcase(queue_name, self.config.reply_prefix):
            return ConsumerSnapshot(queue_name, unit="input handles", note="Internal PCF reply queue excluded.")
        agent = self.inquiry_agent()
        request = J("com.ibm.mq.headers.pcf.PCFMessage")(F.MQCMD_INQUIRE_Q_STATUS)
        request.addParameter(C.MQCA_Q_NAME, queue_name)
        request.addParameter(F.MQIACF_Q_STATUS_TYPE, F.MQIACF_Q_HANDLE)
        request.addParameter(F.MQIACF_OPEN_TYPE, F.MQQSOT_INPUT)
        try:
            responses = agent.send(request)
        except J("com.ibm.mq.headers.pcf.PCFException") as error:
            if int(error.reasonCode) == int(F.MQRCCF_Q_STATUS_NOT_FOUND):
                return ConsumerSnapshot(queue_name, unit="input handles", note=note)
            raise
        consumers = []
        for response in responses:
            def value(identifier, default=""):
                parameter = response.getParameter(identifier)
                if parameter is None:
                    return default
                result = java_value(parameter.getValue())
                return result.strip() if isinstance(result, str) else result
            if value(C.MQCA_Q_NAME) != queue_name or not value(F.MQIACF_OPEN_INPUT_TYPE, 0):
                continue
            channel = value(F.MQCACH_CHANNEL_NAME)
            consumers.append(Consumer(application=value(F.MQCACF_APPL_TAG), remote_address=value(F.MQCACH_CONNECTION_NAME),
                                      user=value(F.MQCACF_USER_IDENTIFIER), protocol="MQI client" if channel else "Bindings",
                                      details={"ProcessId": value(F.MQIACF_PROCESS_ID, "n/a"), "ThreadId": value(F.MQIACF_THREAD_ID, "n/a"),
                                               "Channel": channel or "n/a", "Application description": value(F.MQCACF_APPL_DESC) or "n/a",
                                               "Open input type": value(F.MQIACF_OPEN_INPUT_TYPE), "Open options": value(F.MQIACF_OPEN_OPTIONS, "n/a"),
                                               "Handle state": value(F.MQIACF_HANDLE_STATE, "n/a")}))
        return ConsumerSnapshot(queue_name, sorted(consumers, key=lambda item: (item.application, item.remote_address, str(item.details))), "input handles", note)

    def close(self):
        agent = getattr(self, "pcf_agent", None)
        manager = getattr(self, "inquiry_manager", None)
        try:
            if agent is not None:
                agent.disconnect()
        except Exception:
            pass
        try:
            if manager is not None and manager.isConnected():
                manager.disconnect()
        except Exception:
            pass
        self.pcf_agent = self.inquiry_manager = None
        super().close()


def create_adapter(config):
    return ArtemisAdapter(config) if config.type == "artemis" else IBMAdapter(config)
