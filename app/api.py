import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .service import process_incoming, execute_payouts
from .config import settings
from .db import init_db


class APIHandler(BaseHTTPRequestHandler):
    def _send_json(self, status_code, data):
        body = json.dumps(data).encode("utf-8")

        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "dry_run": settings.dry_run,
                },
            )
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if self.path != "/provider/incoming":
            self._send_json(404, {"error": "Not found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(content_length)
            payload = json.loads(raw_body.decode("utf-8"))

            operation_id = str(payload.get("operation_id", "")).strip()
            sender_email = str(payload.get("sender_email", "")).strip()
            amount = str(payload.get("amount", "")).strip()
            currency = str(payload.get("currency", "USD")).upper()
            raw = payload.get("raw")

            if not operation_id:
                self._send_json(400, {"error": "operation_id is required"})
                return

            if not sender_email:
                self._send_json(400, {"error": "sender_email is required"})
                return

            if not amount:
                self._send_json(400, {"error": "amount is required"})
                return

            if currency != settings.currency.upper():
                self._send_json(
                    400,
                    {"error": "Unsupported currency"},
                )
                return

            result = process_incoming(
                operation_id,
                sender_email,
                amount,
                currency,
                raw,
            )

            if (
                not result.get("duplicate")
                and result.get("status") == "PAYOUT_PENDING"
            ):
                result["payout"] = execute_payouts(
                    result["transaction_id"]
                )

            self._send_json(200, result)

        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})

        except Exception as exc:
            self._send_json(500, {"error": str(exc)})

    def log_message(self, format, *args):
        print(f"[API] {self.address_string()} - {format % args}")


def create_server():
    init_db()
    return ThreadingHTTPServer(
        (settings.host, settings.port),
        APIHandler,
    )
