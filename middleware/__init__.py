from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
import logging
import time

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Simple sliding-window rate limiter.
    Tracks requests per IP address.
    """

    def __init__(self, app, requests_per_window: int = 100, window_seconds: int = 60):
        super().__init__(app)
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self._store: dict[str, list] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/", "/docs", "/openapi.json"]:
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window_start = now - self.window_seconds

        # Clean old entries
        self._store[client_ip] = [t for t in self._store[client_ip] if t > window_start]

        if len(self._store[client_ip]) >= self.requests_per_window:
            logger.warning(f"Rate limit exceeded for IP: {client_ip}")
            return Response(
                content='{"error": "Too many requests. Please try again later."}',
                status_code=429,
                headers={
                    "Content-Type": "application/json",
                    "Retry-After": str(self.window_seconds),
                    "X-RateLimit-Limit": str(self.requests_per_window),
                    "X-RateLimit-Remaining": "0",
                }
            )

        self._store[client_ip].append(now)
        remaining = self.requests_per_window - len(self._store[client_ip])

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_window)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log all incoming requests with timing."""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration_ms = (time.time() - start) * 1000

        logger.info(
            f"{request.method} {request.url.path} "
            f"→ {response.status_code} "
            f"({duration_ms:.1f}ms)"
        )
        return response
