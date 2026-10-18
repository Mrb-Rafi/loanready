"""HttpMessagePort (MessagePort over HTTP). The ticket must be for a send action and bind
the recipient, channel and the hash of the exact body being sent (SBX-A-03)."""

from __future__ import annotations

from adapters.http import ReadClient, WriteClient
from contracts.actions import ExecutionTicket
from contracts.canonical import sha256_hex
from contracts.enums import ActionType
from contracts.errors import TicketInvalid
from contracts.ports import TicketVerifier
from contracts.sandbox_api import MessageOut, MessageSendIn

SEND_TYPES = (ActionType.SEND_REMINDER, ActionType.SEND_DELAY_REQUEST)


class HttpMessagePort:
    def __init__(self, read: ReadClient, write: WriteClient, verifier: TicketVerifier):
        self._read, self._write, self._verify = read, write, verifier

    def send(self, ticket: ExecutionTicket, req: MessageSendIn) -> MessageOut:
        if ticket.action_type not in SEND_TYPES:
            raise TicketInvalid("ticket is not for a message send")
        self._verify(
            ticket,
            ticket.action_type,
            {"to_ref": req.to_ref, "body_sha256": sha256_hex(req.body), "channel": req.channel},
        )
        return MessageOut.model_validate(
            self._write.post("/messaging/send", ticket, json=req.model_dump(mode="json"))
        )

    def list_messages(self) -> list[MessageOut]:
        return [MessageOut.model_validate(m) for m in self._read.get("/messaging/messages")]
