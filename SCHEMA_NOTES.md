# Сверка приложенных схем

Explorer build: `987800-1dd1d2251d190e5`. Reader: `BUILD_EXPECTED = 6356260` (разные форматы идентификаторов).

| Поле | client_dll.json / reader | explorer.html |
|---|---:|---:|
| C_DOTA_BaseNPC.m_Inventory | `0x10C8` | `0x10C0` |
| C_DOTA_BaseNPC.m_nUnitState64 | `0x11C0` | `0x11B8` |
| C_DOTA_BaseNPC.m_flMagicalResistanceValue | `0x1538` | `0x1530` |
| C_DOTABaseAbility.m_fCooldown | `0x638` | `0x638` |
| C_DOTABaseAbility.m_iManaCost | `0x640` | `0x640` |
| C_DOTA_BaseNPC_Hero.m_flIntellectTotal | `0x19F4` | `0x19EC` |

Схемы не совпадают. В дополнении используется только `client_dll.json`, согласованный с константами приложенного reader. Explorer НЕ подмешивается к нему. Это не подтверждает соответствие схемы запущенной игре. При несовпадении общих полей с reader KS блокируется. Глобальные адреса reader не обновлялись.


## KS4.2: подтверждённый live-layout, не поля из Explorer

Две пользовательские серии дали совпадение счётчика `CDOTA_Modifier_Lion_FingerOfDeath_Kill_Counter +0x84` с введёнными 1/2/3. Прямой вектор менеджера count+0x10 / data+0x18 / capacity+0x20 совпал с RTTI-кандидатами. Owner менеджера+0x08 и обратная ссылка модификатора+0x30 подтверждены. Фиксированные heap-адреса не используются. Эти приватные поля не объявляются универсальными для других сборок; включён контроль PE-метаданных и SHA-256 схемы.

`C_DOTA_Ability_Lion_Impale.range` в снимках равен 0 — не применяется для проверки дистанции. Поле `damage_per_kill` у FingerPunch не используется как заместитель величины бонуса Finger of Death.
