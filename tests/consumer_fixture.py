"""Integration helper: hold input/browse handles on DEV.EMPTY, without receiving."""
import os
import sys

from jmsbrowser.brokers import create_adapter
from jmsbrowser.config import load_config


def main():
    os.environ["ARTEMIS_PASSWORD"] = "artemis-local-123"
    os.environ["MQ_APP_PASSWORD"] = "app-local-123"
    adapter = create_adapter(load_config("config.example.yaml").connections[int(sys.argv[1])])
    resources = []
    try:
        adapter.connect()
        if adapter.config.type == "artemis":
            connection = adapter.factory.createConnection(adapter.config.username, adapter.config.password())
            resources.append(connection)
            connection.setClientID("jmsbrowser-integration-consumer")
            session = connection.createSession(False, 1)
            # This connection is never started; the empty-queue selector is additional isolation.
            consumer = session.createConsumer(session.createQueue("DEV.EMPTY"), "JMSCorrelationID = 'jmsbrowser-test-no-match'")
            browser = session.createBrowser(session.createQueue("DEV.EMPTY"))
            enumeration = browser.getEnumeration()
            resources.extend([consumer, browser])
        else:
            import jpype
            C = jpype.JClass("com.ibm.mq.constants.CMQC")
            manager = adapter.queue_manager_connection()
            resources.append(manager.accessQueue("DEV.EMPTY", C.MQOO_INPUT_SHARED | C.MQOO_FAIL_IF_QUIESCING))
            resources.append(manager.accessQueue("DEV.EMPTY", C.MQOO_BROWSE | C.MQOO_FAIL_IF_QUIESCING))
        print("READY", flush=True)
        sys.stdin.readline()
    finally:
        for resource in reversed(resources):
            resource.close()
        adapter.close()


if __name__ == "__main__":
    main()
