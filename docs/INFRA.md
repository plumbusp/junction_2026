# Инфраструктура

Written for: команда. Здесь только не секретные факты. Ключи и пароли лежат в `.env` и SSH-агенте на локальной машине, в репо их нет.

## Сервер

- **Хост:** `srv2160832.hstgr.cloud`, IP `31.220.55.73`. Hostinger VPS KVM 2.
- **ОС:** Ubuntu 24.04.5 LTS. 2 vCPU, 7.8 GB RAM, диск 96 GB.
- **Доступ:** SSH по ключу, пользователь `deploy` (sudo без пароля, группа docker). Вход root только по ключу. Вход по паролю отключён в `/etc/ssh/sshd_config.d/00-hardening.conf`.
- **Firewall (ufw):** открыты 22, 80, 443 и 32768. Остальное закрыто.
- **Docker:** 29.9.3, Compose 5.7.1.

## Что уже работает на сервере

| Контейнер | Что это | Порты | Примечание |
|---|---|---|---|
| `traefik-traefik-1` | reverse proxy от Hostinger, сам выпускает HTTPS (Let's Encrypt) | 80, 443 | конфиг в `/docker/traefik`. Не менять без необходимости |
| `hermes-agent-duo3-hermes-agent-1` | приложение Hermes Agent из шаблона Hostinger | 32768 | опубликовано наружу, ufw Docker не фильтрует |
| `hello-hello-1` | наш тестовый hello-world, `/opt/spotthespin/hello` | нет наружу | доступен только через Traefik |

Публичный адрес hello-world: https://srv2160832.hstgr.cloud (HTTP 200, сертификат Let's Encrypt).

## Как на сервере подключаются новые сервисы

- Наши контейнеры не публикуют порты наружу. Traefik находит их по меткам (`traefik.enable=true`, `Host(...)`, `certresolver=letsencrypt`).
- Шаблон метки: см. `/opt/spotthespin/hello/docker-compose.yml`.
- **Важно:** после обновления или перезапуска Docker нужно перезапустить Traefik: `docker restart traefik-traefik-1`. Иначе сокет Docker внутри контейнера отваливается, и Traefik перестаёт видеть метки.

## Что ещё не сделано и ждёт решения

- **Перезагрузка.** Обновления требуют reboot. Hermes и Traefik перезапустятся, поэтому делаем по согласованию.
- **Hermes на порту 32768.** Сейчас открыт в интернет. Закрыть можно, только поменяв его конфиг публикации порта.
- **Passwordless sudo** у пользователя `deploy`. Удобно для автоматизации, но это вопрос к команде.
- **Dockerfile и `docs/DEPLOY.md`.** Пока нет, появятся вместе с первым реальным приложением.

## Что у нас локально

- `.env` (gitignored): `VPS_HOST`, `VPS_USER=deploy`, `PUBLIC_DOMAIN`, `AI_ROUTER_*`.
- SSH-ключ: приватный ключ хранится только на личной машине и никуда не передаётся. Его публичная часть уже в `authorized_keys` на сервере.
