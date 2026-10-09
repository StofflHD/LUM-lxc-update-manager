# Reverse proxy

[← Back to the README](../README.md)

LUM can run behind any reverse proxy for HTTPS and access from outside. Requirements:

- **Own (sub)domain**, e.g. `lum.example.com`. A sub-path like `example.com/lum/` is **not**
  supported (the UI uses absolute paths).
- **WebSockets** must be passed through (live update logs, path `/ws/…`).
- Pass the public host name: either keep the `Host` header, or send `X-Forwarded-Host`.
  Otherwise the live log is rejected by the origin check.
- Send `X-Forwarded-For` and `X-Forwarded-Proto`, and tell LUM to trust the proxy:
  add its IP to `/opt/lxc-update-manager/.env` and restart the service:

  ```bash
  pct exec <CTID> -- bash -c 'echo FORWARDED_ALLOW_IPS=192.168.1.20 >> /opt/lxc-update-manager/.env && systemctl restart lxc-update-manager'
  ```

  Then the login lockout counts per real client (without it, 5 wrong passwords from
  anyone would lock out everybody behind the proxy), and the session cookie is marked
  `Secure` automatically when the proxy speaks HTTPS.
- Long VM updates send no output for a while; LUM pings the WebSocket every 20 s, so the
  default proxy timeouts are fine.
- Optional: restrict port 8080 of the LUM container (e.g. Proxmox firewall) to the proxy.
- Already a login in front (Authelia, Authentik, …)? Then `LUM_AUTH_DISABLED=true` turns
  off LUM's own login – only if the proxy really protects every path.

### Nginx Proxy Manager

*Hosts → Proxy Hosts → Add Proxy Host*

- **Domain Names:** `lum.example.com`
- **Scheme:** `http`, **Forward Hostname / IP:** IP of the LUM container, **Port:** `8080`
- **Websockets Support:** on
- *SSL* tab: request a certificate, **Force SSL** on

NPM sends `Host`, `X-Forwarded-For` and `X-Forwarded-Proto` by default. Set
`FORWARDED_ALLOW_IPS` to the IP of the NPM container/host.

### nginx

```nginx
map $http_upgrade $connection_upgrade { default upgrade; '' close; }

server {
    listen 443 ssl;
    server_name lum.example.com;
    ssl_certificate     /etc/ssl/lum.example.com/fullchain.pem;
    ssl_certificate_key /etc/ssl/lum.example.com/privkey.pem;

    location / {
        proxy_pass http://192.168.1.50:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }
}
```

### Caddy

```caddy
lum.example.com {
    reverse_proxy 192.168.1.50:8080
}
```

Caddy gets the certificate, passes WebSockets and sets the forwarding headers by itself.

### Traefik (file provider)

```yaml
http:
  routers:
    lum:
      rule: Host(`lum.example.com`)
      entryPoints: [websecure]
      service: lum
      tls:
        certResolver: letsencrypt
  services:
    lum:
      loadBalancer:
        servers:
          - url: http://192.168.1.50:8080
```

Traefik passes WebSockets and the forwarding headers by default.
