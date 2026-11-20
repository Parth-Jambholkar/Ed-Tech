import base64
import hmac
import hashlib
import json
import time
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework import authentication, exceptions

def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')

def base64url_decode(data: str) -> bytes:
    padding = '=' * (4 - (len(data) % 4)) if len(data) % 4 != 0 else ''
    return base64.urlsafe_b64decode((data + padding).encode('utf-8'))

def generate_jwt_for_user(user, expiration_seconds=86400 * 7):
    """Generates standard HMAC-SHA256 JWT for user"""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    payload = {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "iat": now,
        "exp": now + expiration_seconds
    }
    
    header_b64 = base64url_encode(json.dumps(header, separators=(',', ':')).encode('utf-8'))
    payload_b64 = base64url_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
    
    unsigned_token = f"{header_b64}.{payload_b64}"
    signature = hmac.new(
        settings.SECRET_KEY.encode('utf-8'),
        unsigned_token.encode('utf-8'),
        hashlib.sha256
    ).digest()
    signature_b64 = base64url_encode(signature)
    
    return f"{unsigned_token}.{signature_b64}"

def decode_jwt(token: str):
    """Verifies and decodes JWT, returning payload dict or None"""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        header_b64, payload_b64, signature_b64 = parts
        unsigned_token = f"{header_b64}.{payload_b64}"
        
        expected_sig = hmac.new(
            settings.SECRET_KEY.encode('utf-8'),
            unsigned_token.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        actual_sig = base64url_decode(signature_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
            
        payload = json.loads(base64url_decode(payload_b64).decode('utf-8'))
        if payload.get('exp') and time.time() > payload['exp']:
            return None
            
        return payload
    except Exception:
        return None

class JWTOrTokenAuthentication(authentication.BaseAuthentication):
    """
    DRF Authentication class accepting:
    Authorization: Bearer <jwt_token>
    Authorization: Token <jwt_token>
    """
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION')
        if not auth_header:
            return None
            
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() not in ['bearer', 'token']:
            return None
            
        token = parts[1]
        payload = decode_jwt(token)
        if not payload:
            raise exceptions.AuthenticationFailed('Invalid or expired token')
            
        User = get_user_model()
        try:
            user = User.objects.get(id=payload['user_id'], is_active=True)
        except User.DoesNotExist:
            raise exceptions.AuthenticationFailed('User not found or inactive')
            
        return (user, token)
