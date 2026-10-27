# Приёмочные тесты (ТЗ 21)

Каждый пункт покрыт автотестом в `backend/tests`. Запуск: `cd backend && uv run pytest`.

| № | Требование | Тест |
|---|---|---|
| 1 | Пользователь не может подделать Telegram ID или обойти права | `test_init_data.py::test_tampered_user_id`, `::test_api_requires_signed_init_data`; `test_access.py`; `test_acceptance.py::test_permissions_are_enforced_on_every_module` |
| 2 | Один сотрудник не получает задание одновременно с другим | `test_products.py::test_two_employees_never_get_the_same_task` |
| 3 | Повторные запросы не создают дубликаты | `test_products.py::test_idempotency_key_returns_same_task`, `::test_import_preview_errors_duplicates_and_commit`; `test_registration.py::test_concurrent_approve_only_one_wins`; `test_finance.py::test_payout_with_bonus_penalty_and_idempotency`; `test_broadcasts.py::test_audience_filters_and_send` |
| 4 | Таймер нельзя обойти перезагрузкой или подменой клиентского времени | `test_execution.py::test_full_flow_with_stages_timer_and_photos` (время только серверное; повторное «Найдено» не сбрасывает таймер) |
| 5 | Фото считается загруженным только после подтверждения сервера | `test_execution.py::test_full_flow_with_stages_timer_and_photos`, `::test_photo_validation_and_access` (без ответа сервера этап не проходит, файл проверяется по содержимому) |
| 6 | Завершённое задание переходит на модерацию | `test_execution.py::test_full_flow_with_stages_timer_and_photos`; `test_acceptance.py::test_end_to_end` |
| 7 | Ожидающая модерации единица не считается одобренной или отклонённой | `test_execution.py::test_moderation_quality_and_earnings`; `test_stats.py::test_actual_paid_rate_pending_and_overlap` |
| 8 | 10 ед./ч при качестве 50% → 5 оплачиваемых ед./ч | `test_stats.py::test_ten_per_hour_with_half_quality_gives_five` |
| 9 | 8 ед./ч при качестве 95% → 7,6 оплачиваемой ед./ч | `test_stats.py::test_eight_per_hour_with_95_quality_gives_7_6` |
| 10 | Фактическая оплачиваемая производительность — по допущенным единицам и их времени | `test_stats.py::test_actual_paid_rate_pending_and_overlap` |
| 11 | При малой выборке — предупреждение | `test_stats.py::test_ten_per_hour_with_half_quality_gives_five` (`small_sample`) |
| 12 | Выплаты и финансовые операции не дублируются | `test_finance.py::test_payout_with_bonus_penalty_and_idempotency`, `::test_parallel_payouts_not_duplicated` |
| 13 | График соблюдает временные ограничения и вместимость | `test_schedule.py::test_edit_deadlines`, `::test_capacity_and_overlap`, `::test_parallel_requests_for_last_place` |
| 14 | Приглашение отправляется в выбранную группу | `test_groups.py::test_invite_sent_after_telegram_confirms` |
| 15 | Ошибка отправки приглашения не отображается как успех | `test_groups.py::test_invite_failures_are_not_success` |
| 16 | Неизвестное членство не отображается как подтверждённое отсутствие | `test_groups.py::test_newbie_progress_and_membership_rules` |
| 17 | Прогресс новичка корректно меняется с 2/3 на 3/3 | `test_groups.py::test_newbie_progress_and_membership_rules` |
| 18 | После перехода сотрудник проверяется на членство в основной группе | `test_groups.py::test_newbie_progress_and_membership_rules` |
| 19 | Фотоотчёт направляется в группу, назначенную продукту | `test_photoreports.py::test_photo_routed_to_product_and_general_groups` |
| 20 | В начале нового дня создаётся один заголовок фотоотчётов | `test_photoreports.py::test_one_header_per_day_and_replies` |
| 21 | По окончании дня публикуется сводка, в том числе нулевая | `test_photoreports.py::test_zero_summary_and_corrections` |
| 22 | При сбое Telegram фото не теряется | `test_photoreports.py::test_telegram_failure_keeps_photo_and_retry` |
| 23 | Повторные фоновые задачи не создают дубликаты сообщений | `test_photoreports.py::test_one_header_per_day_and_replies`, `::test_scheduler_posts_summary_then_header`; `test_finance.py::test_scheduled_lazy_close_runs_once`; `test_outbox.py::test_dedup_key_prevents_duplicates` |
| 24 | Статистика по продуктам, качеству и заработку соответствует исходным данным | `test_acceptance.py::test_end_to_end` (сверка статистики, журнала, выплаты и дневной сводки) |
| 25 | Изменения исторических записей сохраняют аудит | `test_registration.py::test_audit_is_append_only`, `::test_name_change_flow`; `test_access.py::test_permissions_diff_is_audited`; `test_execution.py::test_moderation_quality_and_earnings` (сторно); `test_photoreports.py::test_zero_summary_and_corrections` (версии сводки) |

Помимо автотестов, основные сценарии проверены в браузере (Playwright, Chromium с
эмуляцией камеры) на реальном API: регистрация и одобрение, продукт и импорт Excel, выполнение
задания с камерой, модерация, график и «Распред», выплата, анкета кандидата, статистика и
выгрузка, рассылка.
