GET /api/v1/repos/lab/pub/raw/src/a.txt?ref=other
> User-Agent: protocol-measure/1
> Accept-Encoding: identity

HTTP 200 OK
Access-Control-Expose-Headers: Content-Disposition
Cache-Control: private, max-age=300
Content-Disposition: inline; filename="a.txt"; filename*=UTF-8''a.txt
Content-Length: 17
Content-Type: text/plain; charset=utf-8
Etag: "67aca029dd89a2a5b9626ea56f5e3f1e821569c5"
Last-Modified: Fri, 16 Oct 2026 17:14:27 GMT
X-Content-Type-Options: nosniff
X-Forgejo-Object-Type: file
X-Frame-Options: SAMEORIGIN
X-Gitea-Object-Type: file
Date: Fri, 16 Oct 2026 17:29:27 GMT

[body: 17 bytes]
one
two on other

