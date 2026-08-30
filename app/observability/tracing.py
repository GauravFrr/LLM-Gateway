from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

import os
# Initialize trace provider
provider = TracerProvider()
if os.getenv("OTEL_CONSOLE_EXPORT", "True").lower() == "true":
    processor = BatchSpanProcessor(ConsoleSpanExporter())
    provider.add_span_processor(processor)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("llm-gateway")


def setup_tracing(app):
    """
    Sets up FastAPI automatic instrumentation for traces.
    """
    FastAPIInstrumentor.instrument_app(app)
