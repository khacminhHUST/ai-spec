"""Khóa CÔNG KHAI của user-management (OAuth HAWEE) – nhúng sẵn, cố ý.

Server MEP không gọi ra được user-management (mạng nội bộ chặn chiều ra IP edge), nên không lấy JWKS lúc chạy được.
Khóa công khai lộ ra là vô hại: chỉ verify, không ký được. Đối chiếu 21/11/2026 với
https://user-management.hawee.com.vn/.well-known/jwks.json: kid = 391bb239b2493539 (16 ký tự hex đầu SHA-256 của
modulus). Token mang kid khác (A đổi khóa) → auth.py thử JWKS động; đặt MCP_SERVER_PUBLIC_KEY_PEM để thay khóa.
"""

PLANNER_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAhNApWhcni6wgJwVutIbQ
w3BpA0JTgeLPhrReOPPsA6lZq2Ml+AG9RBhaWDeWu1DQjM+JNRY0RWOJKkM7V81i
6Ghy5a01NOgRGkEPERa9/VOm05Q5sOeFpt1MWhUNLGx0ZTBTlLWJg8Lo+C2glewr
in9zjFDnJKjsgFj0DLjZVHHsrYlfZPnNd0OXTufjPUApjuHegHnPb0mAf2AYRuUO
HUubVGjd76DaZclqQt5y2hu90rewMRBTmhUISghykcbhskOgzLERkpaF8a9tiqp3
3w9sdOCfgcOWC28WjyRMakUb0QKTvTWHsK3S5qX6+lMRbJNlZogtrweqy+IpicVR
2wIDAQAB
-----END PUBLIC KEY-----
"""
