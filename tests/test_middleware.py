import unittest

from core.middleware import (
    Middleware,
    MiddlewareContext,
    MiddlewarePipeline,
    MiddlewareRejected,
)


class RecordingMiddleware(Middleware):
    def __init__(self, name: str, events: list[str]):
        self.name = name
        self.events = events

    def before(self, context: MiddlewareContext) -> None:
        self.events.append(f"before:{self.name}:{context.operation}")

    def after(self, context: MiddlewareContext, result):
        self.events.append(f"after:{self.name}:{result}")
        return result

    def on_error(self, context: MiddlewareContext, error: BaseException) -> None:
        self.events.append(f"error:{self.name}:{type(error).__name__}")


class MiddlewarePipelineTests(unittest.TestCase):
    def test_before_runs_in_order_and_after_runs_in_reverse(self):
        events = []
        pipeline = MiddlewarePipeline([
            RecordingMiddleware("first", events),
            RecordingMiddleware("second", events),
        ])
        context = MiddlewareContext(operation="tool.execute", payload={"tool": "status"})

        result = pipeline.execute(context, lambda current: current.payload["tool"])

        self.assertEqual(result, "status")
        self.assertEqual(
            events,
            [
                "before:first:tool.execute",
                "before:second:tool.execute",
                "after:second:status",
                "after:first:status",
            ],
        )

    def test_errors_notify_entered_middleware_in_reverse_and_propagate(self):
        events = []
        pipeline = MiddlewarePipeline([
            RecordingMiddleware("first", events),
            RecordingMiddleware("second", events),
        ])

        with self.assertRaisesRegex(RuntimeError, "failed"):
            pipeline.execute(
                MiddlewareContext(operation="agent.run"),
                lambda _context: (_ for _ in ()).throw(RuntimeError("failed")),
            )

        self.assertEqual(
            events,
            [
                "before:first:agent.run",
                "before:second:agent.run",
                "error:second:RuntimeError",
                "error:first:RuntimeError",
            ],
        )

    def test_rejection_is_fail_closed_and_handler_is_not_called(self):
        class RejectingMiddleware(Middleware):
            def before(self, context: MiddlewareContext) -> None:
                context.reject("policy denied")

        called = False

        def handler(_context):
            nonlocal called
            called = True

        with self.assertRaisesRegex(MiddlewareRejected, "policy denied"):
            MiddlewarePipeline([RejectingMiddleware()]).execute(
                MiddlewareContext(operation="tool.execute"),
                handler,
            )

        self.assertFalse(called)

    def test_context_has_stable_correlation_and_mutable_metadata(self):
        context = MiddlewareContext(
            operation="tool.execute",
            correlation_id="run-1",
            causation_id="event-1",
        )
        context.metadata["risk"] = "high"

        self.assertEqual(context.correlation_id, "run-1")
        self.assertEqual(context.causation_id, "event-1")
        self.assertEqual(context.metadata, {"risk": "high"})


if __name__ == "__main__":
    unittest.main()
