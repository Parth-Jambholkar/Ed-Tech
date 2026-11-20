import time
from django.http import HttpResponse, JsonResponse
from collections import defaultdict

# Simple in-memory rate limiter per IP for login and payment routes
RATE_LIMIT_STORE = defaultdict(list)
MAX_REQUESTS_PER_MINUTE = 60

class CorsAndSecurityMiddleware:
    """
    Handles CORS for frontend dev servers
    and enforces rate limiting on login/payment endpoints for security.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Handle Preflight OPTIONS
        if request.method == "OPTIONS":
            response = HttpResponse()
            self._apply_cors_headers(response)
            return response

        # Rate limiting on sensitive endpoints
        if any(target in path for target in ["/api/auth/login/", "/api/orders/create/", "/api/orders/verify/"]):
            client_ip = self._get_client_ip(request)
            now = time.time()
            # Clean requests older than 60 seconds
            RATE_LIMIT_STORE[client_ip] = [t for t in RATE_LIMIT_STORE[client_ip] if now - t < 60]
            if len(RATE_LIMIT_STORE[client_ip]) >= MAX_REQUESTS_PER_MINUTE:
                return JsonResponse({
                    "error": "Rate limit exceeded. Please wait a moment before trying again."
                }, status=429)
            RATE_LIMIT_STORE[client_ip].append(now)

        response = self.get_response(request)
        self._apply_cors_headers(response)
        return response

    def _apply_cors_headers(self, response):
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
        response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With, X-Razorpay-Signature"
        response["Access-Control-Allow-Credentials"] = "true"

    def _get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '127.0.0.1')
