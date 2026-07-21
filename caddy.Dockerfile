# syntax=docker/dockerfile:1

# Hostinger's Compose-from-URL deploy path fetches only docker-compose.yml, never the
# surrounding repo (see docker-compose.yml's top comment) — so a bind-mounted
# ./Caddyfile has no source file on the Hostinger host. Docker's bind-mount behavior
# for a missing source path is to silently create an empty directory there, which then
# fails to mount onto /etc/caddy/Caddyfile (a file) inside the image. Baking the
# Caddyfile in at build time, the same git-context build trick docker-compose.yml
# already uses for the app service, avoids relying on a host path that doesn't exist.
FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
