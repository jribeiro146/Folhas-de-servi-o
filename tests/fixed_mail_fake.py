"""Queue test seam: real preparation/validation, only delivery is substituted."""
import json

from src.services.graph_mail_service import GraphMailConfig, GraphMailService


class FixedMailFake(GraphMailService):
    def __init__(self):
        super().__init__(GraphMailConfig("synthetic", "synthetic", "synthetic", "sender@example.invalid"),
                         transport=self.unexpected_transport)

    @staticmethod
    def unexpected_transport(*args, **kwargs):
        raise AssertionError("Queue fake must not perform transport I/O")

    def message(self, delivery):
        self.validate_delivery(delivery)
        return json.loads(delivery.payload)["message"]

    def operation_id(self, delivery):
        return self.message(delivery)["internetMessageHeaders"][0]["value"]
