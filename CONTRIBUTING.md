# Contributing / Участие

Спасибо за проверку CGB Open Receiver Protocol. Проект принимает небольшие, проверяемые изменения wire-протокола, тестов, документации и независимых примеров. English instructions follow below.

## Перед изменением

1. Для нового устройства заполните [device report](.github/ISSUE_TEMPLATE/device.md), для дефекта — [bug report](.github/ISSUE_TEMPLATE/bug.md).
2. Не прикладывайте закрытый код, credentials, ключи, реальные координаты, MAC/VIN/серийные номера или внутренние пути. Минимизируйте и обезличьте capture.
3. Отделяйте доказанное wire-поведение от сборки, лабораторной проверки и испытания на реальном оборудовании/автомобиле.

## Изменения спецификации

- Меняйте [русскую](spec/spec.ru.md) и [английскую](spec/spec.en.md) редакции вместе; разделы и нормативный смысл должны совпадать.
- Несовместимое изменение требует нового major. В 1.x допустимы только игнорируемые необязательные добавления.
- Не активируйте `rtcm_in` или `cmd_pair`: они RESERVED в 1.0.
- Добавьте запись в [CHANGELOG](CHANGELOG.md) и тестовые векторы для нового правила.
- Все `$…*HH` примеры должны иметь пересчитанный XOR; строки должны быть UTF-8-файлами с LF, wire-строки — ASCII с CRLF.

## Минимальная проверка

Проверьте happy path и границы: bad checksum, 513 байт, побайтовую фрагментацию, duplicate keys, неверный percent-encoding, неизвестные необязательные поля, переполнение `uint32`, reconnect/reboot и отсутствие утечки на cellular/VPN. Для навигации отдельно докажите продвижение UTC; HB не является Fix.

Пример прошивки должен сохранять RX-only UART, ограниченные буферы и `caps=nmea`, пока дополнительные возможности реально не реализованы. Успешная компиляция не считается физической или автомобильной проверкой.

## Pull request

Опишите:

- какое нормативное правило меняется;
- какие RU/EN разделы обновлены;
- команды и результаты проверок;
- что проверено статически, в лаборатории и на реальном устройстве;
- что осталось непроверенным.

By submitting code, you agree to license it under MIT. By submitting specification or README text, you agree to license it under CC BY 4.0, with attribution through the project history.

---

## English

Thank you for validating CGB Open Receiver Protocol. The project accepts small, testable changes to the wire protocol, tests, documentation, and independent examples.

1. Use the [device report](.github/ISSUE_TEMPLATE/device.md) for a new device and the [bug report](.github/ISSUE_TEMPLATE/bug.md) for a defect.
2. Do not attach proprietary code, credentials, keys, real coordinates, MAC/VIN/serial numbers, or internal paths. Minimize and anonymize captures.
3. Distinguish proven wire behavior from compilation, lab checks, and physical/vehicle testing.
4. Update the [Russian](spec/spec.ru.md) and [English](spec/spec.en.md) specifications together, preserving section and normative equivalence.
5. An incompatible change requires a new major; 1.x permits only optional additions old peers can ignore. `rtcm_in` and `cmd_pair` remain RESERVED.
6. Add a [CHANGELOG](CHANGELOG.md) entry and test vectors. Recalculate every XOR example; repository text is UTF-8/LF and wire lines are ASCII/CRLF.
7. Test bad checksum, 513-byte lines, byte fragmentation, duplicate keys, invalid percent encoding, optional unknowns, `uint32` rollover, reconnect/reboot, and no cellular/VPN route escape. Demonstrate advancing UTC separately; HB is not a Fix.
8. Keep example UART physically RX-only and buffers bounded. Advertise only implemented capabilities. Compilation is not physical or vehicle proof.

A pull request should state the normative rule, paired RU/EN sections, commands/results, static/lab/device evidence, and remaining boundary.

By submitting code, you agree to license it under MIT. By submitting specification or README text, you agree to license it under CC BY 4.0, with attribution through the project history.
