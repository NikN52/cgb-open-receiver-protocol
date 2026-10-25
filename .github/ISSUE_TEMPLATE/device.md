---
name: Device compatibility report
about: Report or propose an independent receiver implementation
title: "[Device] "
labels: device
assignees: ""
---

## Устройство / Device

- Плата/приёмник, версия hardware / board/receiver and hardware revision:
- Прошивка и commit/tag / firmware and commit/tag:
- Транспорт / transport: TCP 28495 / UDP push 28497 / SPP / BLE NUS
- Поиск/подписка UDP 28496 (если используется) / discovery/subscription (if used):
- Топология / topology: phone hotspot / head-unit hotspot / other local network
- Объявленные capabilities / advertised capabilities:
- Настроечный псевдоним `id` (обезличенный, не MAC/VIN/serial):

## Статус проверки / Validation status

- [ ] Static review only
- [ ] Builds
- [ ] Lab/bench wire test
- [ ] Physical receiver tested
- [ ] Vehicle tested

Не отмечайте физическую или автомобильную проверку по результату компиляции или эмулятора. / Do not claim physical or vehicle validation from a build or emulator result.

## Результат / Result

- Скорость NMEA и порядок предложений / NMEA rate and sentence order:
- Продвигается ли UTC / does UTC advance:
- Invalid fix выдаёт RMC `V` + GGA quality `0` / invalid-fix behavior:
- HELLO/ACCEPT/HB result (if implemented):
- Discovery/manual IP result:
- Reconnect and reboot result:
- Hotspot isolation / local-route result:
- Remaining limitations:

## Минимальное доказательство / Minimal evidence

Приложите только короткий обезличенный wire-фрагмент и команды проверки. Удалите реальные координаты, SSID/password, IP при необходимости, MAC, VIN, serial, ключи и внутренние пути. / Attach only a short anonymized wire sample and validation commands. Remove real coordinates, credentials, persistent identifiers, keys, and internal paths.

```text
paste sanitized evidence here
```

## Безопасность / Safety

- [ ] UART к GNSS RX-only либо запись обоснована вне профиля 1.0 / receiver UART is RX-only or writes are out of 1.0 scope
- [ ] `rtcm_in` and `cmd_pair` are not advertised
- [ ] No cloud/Internet dependency
- [ ] No cellular/VPN/global route fallback
