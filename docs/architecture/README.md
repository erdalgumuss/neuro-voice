# Architecture — neuro-voice

Mimari dokümanları katmanlı: en üstte **canlı kanonik** spec, altında her bileşenin detay doc'u, en altta **historical referans** (v0.2 single-process MVP).

## Canlı kanonik (v1.0 hedefi)

| Doc | Kapsam |
|---|---|
| [scale-roadmap.md](scale-roadmap.md) | **Tek pencere mimari** — 4 fazlı (A/B/C/D) yol haritası, bileşen seçim matrisi, 15 zorunlu mimari karar (D-01..D-15), min spec'ler, SLO'lar, maliyet projeksiyonu, risk listesi |

scale-roadmap'ten sapan her PR `docs/decisions/README.md`'ye karar satırı + sapma not'u olmadan birleşmez. Tek otorite budur.

## Bileşen detay doc'ları (scale-roadmap'in §19 listesi)

| Doc | Kapsam | Faz |
|---|---|---|
| [data-model.md](data-model.md) | Postgres 16 tam DDL + indices + RLS + migration policy + 7 ORM tablosu | A (bitti) |
| [auth-multi-tenant.md](auth-multi-tenant.md) | API key format + argon2id detayı + JWT operator flow + rate-limit Lua + audit log payload | A (bitti) |
| [streaming-protocol.md](streaming-protocol.md) | WebSocket message types + chunked WAV detayı + SDK örnekleri + handshake | B (WS açık, chunked çalışır) |
| [observability.md](observability.md) | Prometheus metric kataloğu + Grafana dashboard JSON + OTel attribute keys + alert rule'ları | C |
| [voxcpm2-integration.md](voxcpm2-integration.md) | VoxCPM2 API yüzeyi + parametre tuning + Türkçe SFT + per-character LoRA hattı | A-Faz 3 |

## Historical referans

| Doc | Kapsam |
|---|---|
| [platform-v0.2.md](platform-v0.2.md) | Tek-process VoxCPM2 MVP — frontend + filesystem registry + sentence-chunked streaming. Veri planı + multi-tenant ekleri **scale-roadmap.md** ile geçildi. Frontend/registry/engine bölümleri hâlâ doğru |

## Faz checkpoint'leri

| Doc | Konum |
|---|---|
| Faz A audit (lint + dosya) | [../audit/faz-a-self-audit.md](../audit/faz-a-self-audit.md) |
| Faz A audit (mimari + $/saat) | [../audit/faz-a-mlops-audit.md](../audit/faz-a-mlops-audit.md) |
| Faz A çıkışı / Faz B girişi | [../audit/checkpoint-2026-05-24-faz-a-exit.md](../audit/checkpoint-2026-05-24-faz-a-exit.md) |

## Kararlar

Her stratejik karar satır olur: [../decisions/README.md](../decisions/README.md).

## Yön

- **Yeni mimari karar?** → scale-roadmap.md güncellenir + decisions/README.md'ye satır + ilgili detay doc'a yansıtılır (aynı PR'da)
- **Eski bir detay doc'u stale?** → header'ına banner ekle, scale-roadmap kanon, detay doc tamamlayıcı
- **Sadece kod değişikliği, mimari sapma yok?** → doc'a dokunma; karar log'u temiz kalsın
