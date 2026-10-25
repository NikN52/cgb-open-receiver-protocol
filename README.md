# CGB Open Receiver Protocol

[English](#english)

Открытый локальный протокол для подключения собственных GNSS-приёмников, контроллеров и приложений к Car GNSS Bridge. Базовый профиль передаёт NMEA по TCP; авторская прошивка, облако, регистрация и Интернет не нужны.

## Статус

Спецификация **1.0 DRAFT** предлагается для обсуждения. Её wire-формат можно реализовывать и проверять уже сейчас, но отдельный тип приёмника «Открытый протокол» в Car GNSS Bridge — **скоро**; будущая версия или дата не заявлены.

| Возможность | Статус |
|---|---|
| Обычный NMEA по TCP | работает в Car GNSS Bridge 0.27.90 через существующий тип «Сеть TCP» |
| ESP32 UART → Wi-Fi, `caps=nmea` | публичный пример |
| HELLO/ACCEPT/HB, discovery, UDP push, дополнительные capabilities | DRAFT для обсуждения и совместимых реализаций |
| RTCM-ввод и команды | RESERVED, запрещены в 1.0 |

Программная проверка или сборка примера подтверждает только поведение кода в лаборатории. Она не подтверждает электрическую совместимость, качество GNSS/антенны, поведение hotspot, установку в автомобиле или безопасность движения.

## Быстрый старт: работает сейчас

1. Подключите любой одноантенный NMEA-приёмник к RX-only UART платы ESP32 (или аналогичной UART–Wi-Fi платы) и выберите совпадающую скорость `9600`, `38400` либо `115200` бод. Двухантенный приёмник не требуется. Плата подключается как Wi-Fi STA к hotspot телефона или головного устройства, то есть оказывается в одной локальной сети с мостом. Устройство должно слушать TCP `49761` и передавать checksum-valid NMEA с `GGA` и `RMC`; рекомендуется `GGA → GST → RMC`, 5 Гц.
2. В Car GNSS Bridge 0.27.90 откройте «Настройки» → «Приёмник» → «Приёмники…» → «Сеть TCP».
3. Укажите IP устройства и порт `49761`. Для этого режима HELLO, discovery и ACCEPT не нужны.
4. Проверьте продвигающийся UTC, Fix, число спутников, высоту и отсутствие повторов сохранённой точки. При отсутствии решения должны идти RMC `V` и GGA quality `0`.
5. Если соединения нет, проверьте, что hotspot не изолирует клиентов, IP относится к выбранной локальной сети, а трафик не уходит через cellular/VPN.

Пример тестовой эпохи:

```text
$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
$GPGST,123519.000,1.0,2.0,1.5,0.0,1.5,2.0,3.0*68
$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
```

## Открытый профиль

Устройство является TCP-сервером, мост — клиентом. После подключения устройство объявляет pseudonymous `id`, случайный `boot` и реально поддержанные capabilities; мост выбирает их через ACCEPT. `nmea` всегда обязателен. HB показывает только живое соединение и не считается новой координатой.

Минимальный служебный обмен:

```text
$PCGBO,HELLO,pv=1.0,id=demo01,boot=1a2b3c4d,caps=nmea,name=DIY-GNSS,maker=Example,fw=0.1.0,talkers=GP+GN*2D
$PCGBO,ACCEPT,pv=1.0,use=nmea*38
$PCGBO,HB,boot=1a2b3c4d,seq=18,ms=18000,fix=0*67
```

Полные требования, порты, состояния, ограничения, UDP discovery/push, BLE/SPP, PCGBI, UBX и безопасность:

- [Спецификация на русском](spec/spec.ru.md)
- [English specification](spec/spec.en.md)
- [История изменений](CHANGELOG.md)
- [Результаты проверок и их границы](VALIDATION.md)

## Пример и инструменты

- [ESP32 Arduino / PlatformIO](examples/esp32-arduino/) — RX-only UART, Wi-Fi STA, два TCP-клиента, discovery и необязательный mDNS; capability только `nmea`.
- [Независимые wire-примитивы на Python](tools/protocol.py) — стандартная библиотека, checksum/framing, control/PCGBI и UDP validation для тестов.
- [Синтетический локальный приёмник](tools/simulator.py) и [ограниченная 30-секундная проверка устройства](tools/check_device.py). PASS означает только наблюдение в этом запуске; SKIP не является доказательством.
- [Генератор синтетических векторов](tools/generate_vectors.py) и [их воспроизведение](tools/run_vectors.py); инструменты не записывают реальный маршрут.

Быстрая локальная проверка в двух терминалах:

```text
python tools/simulator.py --host 127.0.0.1 --duration 40
python tools/check_device.py 127.0.0.1 --seconds 30 --profile extended
```

Если `49761` недоступен на тестовом ПК, задайте одинаковый альтернативный `--port`, например `49161`, в обеих командах. Это меняет только локальный тест; нормативный порт устройства остаётся `49761`.

Перед прошивкой замените placeholder credentials локально и не публикуйте Wi-Fi пароль, MAC, VIN, серийный номер или личные идентификаторы. Не подключайте 5-вольтовый UART к 3,3-вольтовому входу.

## Обсуждение и вклад

Предложения по дизайну и wire-протоколу размещайте в GitHub Discussions этого репозитория. Для проверенной реализации нового устройства откройте GitHub Issue по [шаблону отчёта об устройстве](.github/ISSUE_TEMPLATE/device.md); для воспроизводимого дефекта — Issue по [шаблону ошибки](.github/ISSUE_TEMPLATE/bug.md). Правила совместного изменения двух языковых редакций и проверок описаны в [CONTRIBUTING](CONTRIBUTING.md).

Не публикуйте закрытый код, ключи, credentials, логи с реальными координатами и устойчивые идентификаторы автомобиля/человека. Для воспроизведения используйте синтетические или явно обезличенные данные.

## Лицензии

- Код, примеры и инструменты: [MIT](LICENSE), Copyright © 2026 Nikolay Pavlov.
- Спецификация и README: [Creative Commons Attribution 4.0 International](LICENSE-SPEC.md), Copyright © 2026 Nikolay Pavlov.

---

<a id="english"></a>

# English

An open local protocol for connecting third-party GNSS receivers, controllers, and applications to Car GNSS Bridge. The baseline profile carries NMEA over TCP; no author-supplied firmware, cloud service, account, or Internet access is required.

## Status

Specification **1.0 DRAFT** is proposed for discussion. Its wire format can already be implemented and tested, but a dedicated “Open Protocol” receiver in Car GNSS Bridge is **coming soon**; no future version or date is promised.

| Feature | Status |
|---|---|
| Ordinary NMEA over TCP | works in Car GNSS Bridge 0.27.90 through the existing “TCP Network” receiver |
| ESP32 UART → Wi-Fi, `caps=nmea` | public example |
| HELLO/ACCEPT/HB, discovery, UDP push, supplemental capabilities | DRAFT for discussion and interoperable implementations |
| RTCM input and commands | RESERVED and forbidden in 1.0 |

A software check or example build proves only laboratory code behavior. It does not prove electrical compatibility, GNSS/antenna performance, hotspot behavior, vehicle installation, or driving safety.

## Quick start: available now

1. Connect any single-antenna NMEA receiver to the RX-only UART of an ESP32 (or a similar UART-to-Wi-Fi board) and select the matching `9600`, `38400`, or `115200` baud rate. No dual-antenna receiver is required. Join the board as a Wi-Fi station to either the phone hotspot or the head-unit hotspot, placing it on the same local network as the bridge. The device listens on TCP `49761` and sends checksum-valid NMEA with `GGA` and `RMC`; `GGA → GST → RMC` at 5 Hz is recommended.
2. In Car GNSS Bridge 0.27.90, open Settings → Receiver → Receivers… → TCP Network.
3. Enter the device IP and port `49761`. This mode does not require HELLO, discovery, or ACCEPT.
4. Verify advancing UTC, Fix, satellite count, altitude, and no replay of a stored position. No solution must be represented by RMC `V` and GGA quality `0`.
5. If it does not connect, check hotspot client isolation, confirm that the IP belongs to the selected local network, and make sure the route cannot escape over cellular/VPN.

Test epoch:

```text
$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
$GPGST,123519.000,1.0,2.0,1.5,0.0,1.5,2.0,3.0*68
$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
```

## Open profile

The device is the TCP server and the bridge is the client. Once connected, the device advertises a pseudonymous `id`, random `boot`, and only capabilities it actually supports; the bridge selects them with ACCEPT. `nmea` is always mandatory. HB proves only a live link and never counts as a new position.

Minimal control exchange:

```text
$PCGBO,HELLO,pv=1.0,id=demo01,boot=1a2b3c4d,caps=nmea,name=DIY-GNSS,maker=Example,fw=0.1.0,talkers=GP+GN*2D
$PCGBO,ACCEPT,pv=1.0,use=nmea*38
$PCGBO,HB,boot=1a2b3c4d,seq=18,ms=18000,fix=0*67
```

Full requirements, ports, states, limits, UDP discovery/push, BLE/SPP, PCGBI, UBX, and security:

- [Russian specification](spec/spec.ru.md)
- [English specification](spec/spec.en.md)
- [Changelog](CHANGELOG.md)
- [Validation results and boundaries](VALIDATION.md)

## Example and tools

- [ESP32 Arduino / PlatformIO](examples/esp32-arduino/) — RX-only UART, Wi-Fi STA, two TCP clients, discovery, and optional mDNS; `nmea` only.
- [Independent Python wire primitives](tools/protocol.py) — standard-library checksum/framing, control/PCGBI, and UDP validation helpers for tests.
- [Synthetic local receiver](tools/simulator.py) and [bounded 30-second device check](tools/check_device.py). PASS means only an observation in that run; SKIP is not evidence.
- [Synthetic vector generator](tools/generate_vectors.py) and [vector replay](tools/run_vectors.py); the tools do not record a real route.

Quick local check in two terminals:

```text
python tools/simulator.py --host 127.0.0.1 --duration 40
python tools/check_device.py 127.0.0.1 --seconds 30 --profile extended
```

If `49761` is unavailable on the test PC, pass the same alternate `--port`, for example `49161`, to both commands. This changes only the local test; the normative device port remains `49761`.

Replace placeholder credentials locally before flashing. Do not publish a Wi-Fi password, MAC, VIN, serial number, or personal identifier. Never connect a 5 V UART output to a 3.3 V-only input.

## Discussion and contribution

Use this repository’s GitHub Discussions for design and wire-protocol proposals. For a validated new implementation, open a GitHub Issue with the [device report template](.github/ISSUE_TEMPLATE/device.md); for a reproducible defect, use the [bug template](.github/ISSUE_TEMPLATE/bug.md). See [CONTRIBUTING](CONTRIBUTING.md) for paired RU/EN changes and validation rules.

Do not publish proprietary code, keys, credentials, logs with real coordinates, or persistent vehicle/person identifiers. Use synthetic or explicitly anonymized evidence.

## Licenses

- Code, examples, and tools: [MIT](LICENSE), Copyright © 2026 Nikolay Pavlov.
- Specification and README: [Creative Commons Attribution 4.0 International](LICENSE-SPEC.md), Copyright © 2026 Nikolay Pavlov.
