# Açık-Ağırlıklı TTS Manzarası — Şubat 2025 → Mayıs 2026

**Araştırmacı:** Atlas (Claude)
**Tarih:** 2026-05-19
**Kapsam:** Alan 1 (state-of-the-art açık-ağırlıklı TTS modelleri) + Alan 5 (düşük-latency streaming)
**Üst dosya:** `neeko-voice/docs/research/00-research-brief.md`

## Araştırma Metodolojisi

Bu rapor için taranan kaynaklar:

- **Hakemli / önbasım**: arxiv.org (F5-TTS 2410.06885, CosyVoice 2 2412.10117, MaskGCT 2409.00750, VoXtream 2509.15969, Step-Audio-EditX 2511.03601)
- **Resmi model kartları**: HuggingFace model pages (Kokoro-82M, Voxtral-4B-TTS-2603, Magpie-TTS Multilingual 357M, IndexTeam/Index-TTS, ResembleAI/chatterbox, marduk-ra/F5-TTS-Turkish)
- **Resmi repo'lar**: GitHub (SWivid/F5-TTS, FunAudioLLM/CosyVoice, fishaudio/fish-speech, stepfun-ai/Step-Audio-EditX, resemble-ai/chatterbox, SparkAudio/Spark-TTS, RVC-Boss/GPT-SoVITS, rhasspy/piper, livekit/agents, pipecat-ai/pipecat, KoljaB/RealtimeTTS, boson-ai/higgs-audio)
- **Leaderboard**: huggingface.co/spaces/TTS-AGI/TTS-Arena-V2, artificialanalysis.ai/text-to-speech
- **Şirket blog'ları**: mistral.ai/news/voxtral-tts (Mart 2026), fish.audio/blog (Mart 2026), boson.ai/blog/higgs-audio-v2 (Tem 2025), resemble.ai/introducing-chatterbox-multilingual, baseten.co/blog (XTTS streaming)
- **Bağımsız analizler**: bentoml.com (2026), modal.com/blog, marktechpost.com (Kasım 2025, Mart 2026)

**Atlanan kaynaklar**: HuggingFace API gating yüzünden bazı sayfalar (Chatterbox multilingual model card) doğrudan çekilemedi → ikincil kaynaklarla doğrulandı.

**Karar üretme tonu**: Bu rapor karar destekleyici. Her bölüm somut "Bize özel öneri" ile kapanır. Ansiklopedi değil.

---

## Alan 1 — Açık-Ağırlıklı TTS State-of-the-Art

### TL;DR

1. **Mayıs 2026 itibarıyla TTS Arena V2 leaderboard tepesindeki beş açık-ağırlık modeli**: Fish Audio S2 Pro (Elo 1128), Step-Audio-EditX (1107), Magpie-TTS Multilingual 357M (1065), Voxtral TTS (1058), Kokoro-82M v1.0 (1056) [`tts-agi-tts-arena-v2.hf.space/leaderboard`, Nis 2026].
2. **Lisans gerçeği**: Bu listenin yarısı ticari kullanım için engelli (Voxtral CC-BY-NC, MaskGCT CC-BY-NC, Spark-TTS CC-BY-NC-SA, F5-TTS base CC-BY-NC, IndexTTS commercial-license-needed, ChatTTS model CC-BY-NC). Apache 2.0 / MIT açık-ticari olanlar: Fish Audio S2-Pro, Kokoro-82M, CosyVoice 2, Chatterbox, Higgs Audio v2, OpenVoice v2, MeloTTS, StyleTTS 2, Parler-TTS.
3. **Türkçe gerçeği daha sert**: Yalnızca üç model "out-of-the-box" Türkçe iddiası taşıyor — **Chatterbox Multilingual** (23 dil, Türkçe dahil), **Bark** (13 dil, Türkçe listede), **Piper** (40+ dil, Türkçe model mevcut ama Windows'ta özel karakter bug'ı raporlanmış). Geri kalanlar için Türkçe ya community fine-tune (F5-TTS-Turkish, Common Voice 17 üzerinden 5.5k örnek) ya da hiç yok.
4. **Mimari dönüşüm**: Şubat 2025 öncesi flow-matching (F5, MaskGCT) hakimdi; Şubat 2025 sonrası "AR semantic + flow-matching acoustic" hibrit yapıları (Voxtral, CosyVoice 2, IndexTTS-2) ile "LLM-grade audio token" yaklaşımı (Step-Audio-EditX, Higgs Audio v2) öne çıktı. Bizim için anlamı: streaming/edit-edilebilirlik artık ücretsiz değil — mimari seçim şart.
5. **Karar yönü**: Lisans + Türkçe filtreleri **Chatterbox Multilingual** (MIT, native Türkçe, voice cloning), **Kokoro-82M** (Apache 2.0, hızlı, küçük; Türkçe yok ama edge için fallback) ve **CosyVoice 2** (Apache 2.0, streaming-native, multilingual ama Türkçe yok — fine-tune adayı) üçlüsünü bizim için ön sıraya itiyor. ElevenLabs'tan çıkışın tek ciddi adayı **Chatterbox Multilingual**.

---

### 1.1 Lisans Filtresi — Ticari Kullanıma Açık Mı?

Neeko ürün şirketi. **Non-commercial lisanslar baştan eler.** Aşağıdaki tabloda kritik 18 model. "Ticari" sütunu sadece model ağırlıkları içindir — kod ayrıca farklı lisanslı olabilir.

| Model | Kod | Model Ağırlık | Ticari Kullanım | Kaynak |
| --- | --- | --- | --- | --- |
| **Kokoro-82M** | Apache 2.0 | Apache 2.0 | **Evet** | huggingface.co/hexgrad/Kokoro-82M |
| **Chatterbox / Multilingual** | MIT | MIT | **Evet** | github.com/resemble-ai/chatterbox |
| **CosyVoice 2 (0.5B)** | Apache 2.0 | Apache 2.0 | **Evet** | github.com/FunAudioLLM/CosyVoice |
| **Fish Audio S1 / S2-Pro** | Apache 2.0 / Fish Research | "Research License" — S2-Pro Mar 2026 açıkça açıldı, S1 Apache | **Kısmi** (S1 evet, S2 belirsiz) | fish.audio/blog/fish-audio-open-sources-s2 (Mar 2026) |
| **Higgs Audio v2** | Apache 2.0 | Apache 2.0 | **Evet** | boson.ai/blog/higgs-audio-v2 (Tem 2025) |
| **OpenVoice v2** | MIT | MIT | **Evet** | huggingface.co/myshell-ai/OpenVoiceV2 |
| **MeloTTS** | MIT | MIT | **Evet** | github.com/myshell-ai/MeloTTS |
| **StyleTTS 2** | MIT | MIT | **Evet** (ses-onayı şartı var) | github.com/yl4579/StyleTTS2 |
| **Parler-TTS** | Apache 2.0 | Apache 2.0 | **Evet** | huggingface.co/parler-tts |
| **Piper** | MIT | MIT (model bazlı bazıları CC) | **Evet** (çoğu) | github.com/rhasspy/piper |
| **Sesame CSM-1B** | Apache 2.0 | Apache 2.0 | **Evet** | huggingface.co/sesame/csm-1b (Mar 2025) |
| **XTTS-v2** (Coqui) | MPL 2.0 (kod) | CPML (custom non-commercial without paid license) | **Hayır** (Coqui Oca 2024 kapandı, lisans belirsizliği) | github.com/coqui-ai/TTS/discussions/4304 |
| **F5-TTS** (base) | MIT (kod) | CC-BY-NC 4.0 (Emilia dataset zorluğu) | **Hayır** | github.com/SWivid/F5-TTS/discussions/997 |
| **Voxtral 4B TTS (Mistral)** | — | CC-BY-NC 4.0 | **Hayır** (Mistral ile anlaşma gerekli) | huggingface.co/mistralai/Voxtral-4B-TTS-2603 (Mar 2026) |
| **MaskGCT** | MIT (kod) | CC-BY-NC 4.0 | **Hayır** | huggingface.co/amphion/MaskGCT |
| **Spark-TTS** | (kod açık) | CC-BY-NC-SA 4.0 | **Hayır** | github.com/SparkAudio/Spark-TTS |
| **IndexTTS / IndexTTS-2** | Apache 2.0 (kod) | Commercial license needs request (indexspeech@bilibili.com) | **Sınırlı** | github.com/index-tts/index-tts |
| **ChatTTS** | AGPLv3+ | CC-BY-NC 4.0 | **Hayır** | github.com/2noise/ChatTTS |
| **Bark** (Suno) | MIT | MIT (eski EnCodec endişesi giderildi) | **Evet** | github.com/suno-ai/bark |
| **Tortoise** | Apache 2.0 | Apache 2.0 | **Evet** (yavaş, RTF 0.25) | github.com/neonbjb/tortoise-tts |
| **NVIDIA Magpie-TTS Multilingual** | — | NVIDIA Open Model License | **Evet** (NVIDIA OML şartlı ticari) | huggingface.co/nvidia/magpie_tts_multilingual_357m |
| **GPT-SoVITS** | MIT | MIT | **Evet** | github.com/RVC-Boss/GPT-SoVITS |
| **Step-Audio-EditX** | Apache 2.0 | Apache 2.0 | **Evet** | github.com/stepfun-ai/Step-Audio-EditX (Kas 2025) |

**Bizim için anlamı**: Listeyi ticari filtreyle keserek 12 model kalıyor. Mayıs 2026 Arena leaderboard top-5'inden sadece **Fish S2-Pro**, **Step-Audio-EditX** ve **Kokoro-82M** ticari elendi sonrasında ayakta kalıyor — Voxtral ve Magpie düşüyor (Voxtral CC-BY-NC, Magpie NVIDIA-OML şartlı).

> **Bize özel filtre sonucu (12 ticari aday)**: Chatterbox Multilingual, Kokoro-82M, CosyVoice 2, Fish S2-Pro (S2-Pro Mar 2026 açıldı, doğrulanmalı), Higgs Audio v2, Step-Audio-EditX, OpenVoice v2, MeloTTS, StyleTTS 2, Parler-TTS, Piper, Bark, Tortoise, GPT-SoVITS.

### 1.2 Türkçe Desteği — Resmi mi, Multilingual Yan Etkisi mi?

Çocuk-yönelimli karakter AI oyuncağı için **doğal Türkçe pronunciation** non-negotiable. "Kanıt" yerine "gözlemlenebilir Türkçe örnek" arıyoruz.

| Model | Resmi Türkçe Desteği | Gerçek Türkçe Örnek (URL) | Not |
| --- | --- | --- | --- |
| **Chatterbox Multilingual** | Evet (23 dil resmi liste) | resemble.ai/introducing-chatterbox-multilingual-open-source-tts-for-23-languages, Hugging Face Space `ResembleAI/Chatterbox-Multilingual-TTS` | Türkçe "diğer dillerle tutarlı kalite" iddiası (Resemble blog); zero-shot voice cloning Türkçe için de çalışıyor |
| **Bark** (Suno) | Evet (13 dil resmi liste, Türkçe dahil) | github.com/suno-ai/bark | Eski (Nis 2023); pronunciation güvenilirsiz, halüsinasyon yüksek |
| **Piper** | Evet (`tr_TR-dfki-medium`, `tr_TR-fettah-medium` modelleri) | rhasspy.github.io/piper-samples (Türkçe sample dinlenebilir) | CPU-friendly, ama tek-konuşmacı + üretken değil (VITS); voice cloning yok |
| **F5-TTS (community)** | Hayır (base CN+EN), community fine-tune var | `marduk-ra/F5-TTS-Turkish` voca.ro/1nM46muVinRS demo (24 Eki 2025), Common Voice 17 5.57k örnek üzerinde | CC-BY-NC, ticari değil |
| **GPT-SoVITS** | README'de TR çeviri var, run-time TR desteği belirsiz | github.com/RVC-Boss/GPT-SoVITS | "Cross-lingual inference" iddiası ama Türkçe doğrulayıcı örnek bulamadım |
| **CosyVoice 2** | Hayır (CN/EN/JA/KO/yh) | — | Çinli ekosistem; Türkçe için fine-tune gerek |
| **Kokoro-82M** | Hayır (EN/JA/ZH/FR/HI/ES/IT/PT) | — | Türkçe yok |
| **Voxtral 4B** | Hayır (9 dil: EN/FR/DE/ES/NL/PT/IT/HI/AR) | — | Türkçe yok |
| **Magpie 357M** | Hayır (9 dil: EN/FR/ES/DE/VI/IT/ZH/HI/JA) | — | Türkçe yok |
| **MaskGCT** | Hayır (6 dil) | — | Türkçe yok + CC-BY-NC |
| **OpenVoice v2 / MeloTTS** | Hayır (EN/ES/FR/ZH/JA/KO) | — | Türkçe yok |
| **Higgs Audio v2** | Multilingual iddia, dil listesi resmi yayınlanmadı | github.com/boson-ai/higgs-audio | Doğrulayıcı Türkçe örnek bulamadım |
| **Step-Audio-EditX** | Hayır (CN/EN + sichuanese + cantonese) | — | Türkçe yok |
| **Fish S1/S2-Pro** | "80+ dil" iddiası (S2-Pro Mar 2026) | fish.audio/blog/fish-audio-open-sources-s2 | Spesifik Türkçe sample bulamadım, doğrulanması gerek |
| **StyleTTS 2** | "14 dilli PL-BERT" iddiası; Türkçe açıkça doğrulanmadı | github.com/yl4579/StyleTTS2 | Doğrulayıcı kaynak bulamadım |
| **Parler-TTS Mini Multilingual v1.1** | 8 dil (EN/FR/DE/ES/IT/NL/PT/PL) | huggingface.co/parler-tts/parler-tts-mini-multilingual-v1.1 | Türkçe yok |

**Bizim için anlamı**: Türkçe + ticari + 2025/2026 modern mimari filtresinin kesişiminde **tek "hazır" aday Chatterbox Multilingual**. İkincil: **Piper** (klasik VITS, CPU edge için iyi ama prosodik ifade düşük). Üçüncü yol: **CosyVoice 2 / Kokoro / Higgs / Fish S2-Pro üzerine Türkçe fine-tune** — riskli, en az 30-100 saat clean Türkçe veri ve düzgün PL-BERT/G2P gerek.

### 1.3 Mimari Karşılaştırması

Mimari seçimi sadece kalite değil, **streaming, edit, latency** davranışını belirler. Aşağıdaki tablo lisans + Türkçe filtresinden geçen 12 modelin teknik profilini özetler.

| Model | Mimari | Boyut | Inference VRAM | LoRA FT VRAM | RTF (GPU) | TTFB stream | HF stars (yak.) | Son aktivite |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Kokoro-82M | StyleTTS2-türevi VITS-style | 82M | <1 GB | 4-8 GB | ~0.02 (550x CPU sonrası quant) | ~100-200 ms | 4k+ | Aktif (Apr 2025) |
| Chatterbox Multilingual | AR (Llama-tabanlı) + flow vocoder | 0.5B | 4-8 GB | 12-24 GB | ~0.15-0.30 | ~150-300 ms (Turbo varyant) | 3k+ | Aktif (Mar 2026) |
| CosyVoice 2 (0.5B) | LLM (AR semantic) + CFM acoustic + Mimi | 0.5B | 4-8 GB | 16-24 GB | ~0.2 | **150 ms first packet** | 12k+ | Aktif (Mar 2026) |
| Higgs Audio v2 | Llama foundation + audio tokenizer | ~3B | 8-16 GB | 24+ GB | streaming via vLLM | sub-300ms (rapor) | 2k+ | Aktif (Tem 2025) |
| Fish S2-Pro (OpenAudio) | AR LLM + emotion control | ~1.5B | 6-12 GB | 16-24 GB | "production streaming" | <300 ms iddia | 18k+ (fish-speech repo) | Aktif (Mar 2026) |
| Step-Audio-EditX | LLM-tabanlı audio token | 3B | 8-16 GB | 24+ GB | iterative edit; AR | AR frame-by-frame | 1k+ | Aktif (Kas 2025) |
| Bark | Transformer + EnCodec | 1B | 8-12 GB | 16+ GB | RTF 0.5x (yavaş) | yok (full-utterance) | 36k+ | Stale (2023) |
| OpenVoice v2 | TTS + tone color converter | ~80M base + converter | 4-6 GB | 8 GB | ~0.1 | yok | 30k+ | Stagnant (2024) |
| MeloTTS | VITS + BERT | ~140M | 2-4 GB | 6-8 GB | gerçek-zamanlı CPU | streaming değil | 5k+ | Stagnant (2024) |
| StyleTTS 2 | Flow + style diffusion + adversarial | 300M | 4-8 GB | 12-16 GB | ~0.05 | streaming değil (full-utt) | 5k+ | Stale (2024) |
| Parler-TTS | T5-style AR | 880M (large-v1) | 6-10 GB | 16-24 GB | streaming destekli | sub-500ms | 5k+ | Stagnant (2024 v1.1) |
| Piper | VITS + ONNX | ~30-60M (medium) | CPU-only ok | Custom training | RTF <0.1 CPU | streaming chunked yok ama hızlı | 9k+ | Aktif |
| GPT-SoVITS | GPT semantic + SoVITS acoustic | ~300M | 4-8 GB | 12 GB | ~0.2 | AR streaming mümkün | 40k+ | Aktif |
| Tortoise | AR + diffusion vocoder | ~1B | 8-12 GB | — | RTF 0.25-0.3 (çok yavaş) | yok | 13k+ | Stale (2023) |
| Sesame CSM-1B | Llama backbone + Mimi tokens | 1B | 8-12 GB | 16-24 GB | TTFB <500 ms (community report) | streaming destekli | 2k+ | Aktif (Mar 2025) |

**Kaynaklar (sayısal değerler)**:
- Kokoro CPU 550x: spheron.network/blog/deploy-open-source-tts-gpu-cloud-2026
- CosyVoice 2 150ms first packet: funaudiollm.github.io/cosyvoice2 + arxiv 2412.10117 (Ara 2024)
- F5-TTS RTF 0.15: arxiv.org/abs/2410.06885 (Eki 2024)
- XTTS-v2 streaming 200ms TTFB: baseten.co/blog/streaming-real-time-text-to-speech-with-xtts-v2
- Voxtral 70ms model latency: mistral.ai/static/research/voxtral-tts.pdf (Mar 2026)

**Bizim için anlamı**: **CosyVoice 2** ve **Chatterbox Multilingual** "modern hibrit (AR semantic + flow acoustic)" jenerasyonun ticari-açık aday üyeleri. Bu mimari Tonies-benzeri karakter sesi (5-10 sn referans ile zero-shot clone) + canlı diyalog (streaming) için tasarlanmış. Eski VITS-türevi (Piper, MeloTTS, Kokoro) hızlı ama karakterli ses üretmiyor — sabit-konuşmacı.

> **Bize özel öneri (Alan 1)**:
>
> 1. **Birincil aday: Chatterbox Multilingual.** MIT lisans + 23 dil resmi Türkçe + voice cloning + ResembleAI benchmark (Chatterbox ElevenLabs'tan %63.75 win-rate) [`resemble.ai/chatterbox`]. POC adımı: HF Space `ResembleAI/Chatterbox-Multilingual-TTS` üzerinde 5-10 Türkçe çocuk-karakter cümlesini "Erdal'ın voice prompt'u" ile dene, ses-mühendis kulağıyla değerlendir.
> 2. **İkincil aday: CosyVoice 2 + Türkçe fine-tune.** Apache 2.0 + streaming-native + 150ms TTFB. Yatırım: 30-60 saat clean Türkçe TTS verisi (Common Voice 17 TR + DepMap + studio recording karışımı), 1-2 hafta fine-tune iterasyonu. Risk yüksek ama uzun vadede tam bağımsızlık.
> 3. **Fallback / edge: Piper.** Türkçe modeli var (`tr_TR-dfki-medium`), CPU'da gerçek-zamanlı, ESP32 değil ama Raspberry Pi 4 sınıfı edge için kullanılabilir. Karakter sesi yok → Neeko karakterleri için yetersiz, ama "ebeveyn paneli", "saat söyleme", "uyku modu metni" gibi nötr çıktılar için maliyet sıfır.
> 4. **Eleme: F5-TTS, Voxtral, MaskGCT, Spark-TTS, ChatTTS, IndexTTS, Coqui XTTS-v2.** Lisans engelli. F5-TTS-Turkish base'i CC-BY-NC olduğu için fine-tune sonucu da ticari değil.
> 5. **Karar süresi**: Chatterbox üzerinde **maks. 1 hafta POC**. Türkçe çocuk-dostu prozod yeterli mi yetersiz mi belirsiz; ses kalitesi kabul ederse direkt entegrasyona; etmezse CosyVoice 2 fine-tune (uzun yol) ya da ElevenLabs'ta kalma (drop dead alternatif).

---

## Alan 5 — Düşük-Latency Streaming TTS

### TL;DR

1. Sayısı az değil ama **"gerçek streaming" yapanlar**: CosyVoice 2 (bidirectional, 150ms first packet), XTTS-v2 (Coqui ölü ama kod yaşıyor, 200ms first byte), Chatterbox-Turbo, Voxtral TTS (70ms model latency), Higgs Audio v2 (vLLM-backed), Sesame CSM-1B, Parler-TTS, ChatTTS, RealtimeTTS via Kokoro chunked.
2. **Yapamayanlar**: Saf flow-matching (F5-TTS, MaskGCT) tek-shot ODE çözücü kullanıyor — chunked emission değil. StyleTTS 2, MeloTTS, OpenVoice v2, Bark, Tortoise, Piper full-utterance üretiyor (Kokoro hariç klasik VITS / NAR çoğunluğu streaming kabiliyetli değil ama yeterince hızlı oldukları için "psödo-streaming" yapılabiliyor).
3. **Çerçeve manzarası**: **LiveKit Agents** + **Pipecat** voice-agent orchestration için fiili-standart. WebRTC üzerinden TTS audio frame'leri taşınıyor; TTS provider'lar Pipecat/LiveKit Service abstraction'a takılıyor. Açık kaynak TTS'i bu çerçevelere bağlamak için zaten XTTS, Cartesia, Kokoro adapter'ları mevcut.
4. **Edge inference durumu**: ONNX Runtime + INT8 quantization tipik 3-4x speedup veriyor [opensource.microsoft.com/blog/2022/05/02/optimizing-and-deploying-transformer-int8-inference-with-onnx-runtime-tensorrt]. **4-8GB VRAM edge cihazda çalışabilenler**: Kokoro-82M (<1GB), MeloTTS, Piper (CPU), OpenVoice v2 küçük modeller, Chatterbox Turbo (quantize). CosyVoice 2 / Voxtral / Higgs / Step-Audio bu sınıfa girmiyor — bulutta kalmalı.
5. **Karar yönü**: Neeko v1 (Waveshare RK35x edge donanım, 4-8GB RAM) için **tamamen edge TTS şu an gerçekçi değil**. Ya cloud TTS + WebRTC streaming (Chatterbox veya CosyVoice 2 sunucuda, LiveKit ile audio frame transport) ya da hybrid: edge'de fallback küçük Piper + bulutta karakter sesi.

---

### 5.1 Gerçek Streaming Yapabilen Modeller

"Gerçek streaming" tanımı: ilk text chunk geldiği anda audio chunk emission başlıyor ve text geldikçe devam ediyor (chunked, token-streaming, AR frame-by-frame).

| Model | Streaming Türü | Stream Mekaniği | First-byte / chunk latency | Kaynak |
| --- | --- | --- | --- | --- |
| **CosyVoice 2** | Bidirectional streaming | LLM AR semantic + CFM chunked acoustic | **150 ms first packet** | funaudiollm.github.io/cosyvoice2, arxiv 2412.10117 |
| **XTTS-v2** | Streaming endpoint | Chunked emission, Baseten sunum | 200 ms first byte, <150 ms consumer GPU | baseten.co/blog/streaming-real-time-text-to-speech-with-xtts-v2 |
| **Voxtral TTS 4B** | Streaming-native | AR semantic + flow acoustic, hybrid | 70 ms model latency (10s sample, 500 char input) | mistral.ai/static/research/voxtral-tts.pdf (Mar 2026) |
| **Chatterbox / Turbo** | Streaming destekli | Llama-tabanlı AR | <300 ms (Turbo "fastest open-source inference" iddia) | resemble.ai/chatterbox |
| **Higgs Audio v2** | vLLM streaming | OpenAI-compat API | "sub-second" rapor edildi | boson.ai/blog/higgs-audio-v2, github.com/boson-ai/higgs-audio |
| **Sesame CSM-1B** | Streaming destekli | Llama backbone + Mimi codec | sub-500ms (community report) | huggingface.co/sesame/csm-1b |
| **Parler-TTS** | Streaming via SDPA + torch.compile | AR T5-style | sub-500ms | github.com/huggingface/parler-tts |
| **ChatTTS** | Streaming destekli | AR | sub-second (rapor) | github.com/2noise/ChatTTS (model CC-BY-NC, ticari değil) |
| **GPT-SoVITS** | AR semantic streaming mümkün | GPT semantic + SoVITS | sub-second | github.com/RVC-Boss/GPT-SoVITS |
| **VoXtream** (arxiv 2509.15969) | "Full-stream extremely low latency" | Yeni arxiv (Eyl 2025) | aşırı düşük (model release durumu belirsiz) | arxiv.org/html/2509.15969v1 |
| **Step-Audio-EditX** | AR token edit | Iterative AR | AR frame-by-frame | arxiv.org/abs/2511.03601 |

#### Yapamayan / sınırlı modeller

| Model | Neden | Notu |
| --- | --- | --- |
| **F5-TTS** | Tek-shot flow matching, ODE solver tüm sequence'i bir hamlede çözer | Streaming için "step reduction" araştırması devam ediyor (arxiv 2410.06885 limitations) |
| **MaskGCT** | NAR masked generation, paralel ama full-utterance | Cumulative emission değil |
| **StyleTTS 2** | Style diffusion + adversarial, tek-shot | Streaming yok |
| **MeloTTS / OpenVoice v2** | VITS-türevi, tek-shot waveform | Hızlı ama chunked değil; psödo-streaming sentence-bazlı yapılabilir |
| **Bark** | Transformer + EnCodec, full-utterance | Yavaş + non-streaming |
| **Tortoise** | AR + diffusion vocoder, çok yavaş | Streaming değil |
| **Piper** | VITS, full-utterance ama RTF <0.1 CPU | "Practical streaming" sentence-bazlı parçala-çal mümkün |
| **Kokoro-82M** | Saf VITS değil — KOKORO_STREAM=true + chunk size 50 ile **chunked streaming destekli** (Kokoro-FastAPI sunucusunda) | 1-2 sn time-to-first-audio (ttsinsider.com/kokoro-82m-onnx, github.com/remsky/Kokoro-FastAPI) |

**Bizim için anlamı**: "Gerçek streaming + ticari + Türkçe" kesişiminde **net kazanan yok**. Chatterbox/Turbo streaming destekliyor ve Türkçe konuşuyor ama ResembleAI'nin "Turbo" tabanı ana model kadar uzun stream tutmuyor. CosyVoice 2 streaming-native ama Türkçe için fine-tune lazım. XTTS-v2 streaming şampiyonu ama lisans ölü. **Pragmatik yol: Chatterbox Multilingual + chunked text feed (cümle-bazlı emit) — saf streaming değil ama TTFB 300-500 ms erişilebilir.**

### 5.2 Autoregressive vs Flow-Matching — Streaming Davranış Farkı

- **AR (Autoregressive) modeller** (CosyVoice 2 semantic kısmı, Chatterbox, Voxtral semantic, Higgs, Step-Audio, GPT-SoVITS, Tortoise, Bark): Sequence token-by-token üretir, **doğal olarak streaming uyumlu**. Her token oluşunca audio chunk yayınlanabilir. Trade-off: hata birikimi (drift), uzun sequence'lerde kalite düşüşü.
- **Flow-matching modeller** (F5-TTS, MaskGCT, CosyVoice 2 acoustic kısmı, Voxtral acoustic): Diffusion-türevi, **N-step ODE solver** ile noise → mel transformation. Streaming için modeli "chunk-conditioned" hale getirmek gerek; F5-TTS topluluğu "step reduction + sway sampling" ile latency düşürdü ama hala full-utterance jenerasyona yakın. **Saf flow-matching = streaming değil.**
- **Hibrit (AR semantic + flow acoustic)**: 2025-2026 yeni nesil (Voxtral, CosyVoice 2, IndexTTS-2). Semantic AR streaming + acoustic chunked CFM = best-of-both. **Bizim mimari hedefimiz.**

> **Bize özel öneri**: Tek bir mimariye değil, **AR semantic + flow acoustic hibridine kilitlen**. Chatterbox bu örüntüye uyuyor (Llama + flow vocoder); CosyVoice 2 zaten kanonik örnek. Saf flow-matching (F5-TTS) Neeko'nun realtime diyalog hedefiyle uyumsuz; karaoke / audiobook tarzı uzun-form üretim hariç eleme.

### 5.3 Framework + WebRTC Entegrasyonu

LiveKit Agents ve Pipecat artık "voice-agent stack" üzerinde fiili standartlar. Hangi TTS'i seçersek seçelim bu iki ekosistemle entegrasyon kabuğunu kullanacağız.

| Çerçeve | Rolü | Açık TTS adapter'ları | Kaynak |
| --- | --- | --- | --- |
| **LiveKit Agents** | Voice-agent + WebRTC transport | TTS adapter ekosistemi geniş; Cartesia, ElevenLabs, OpenAI, plus self-hosted özel TTS (custom integration via TTSPlugin) | docs.livekit.io/agents/models/tts/, github.com/livekit/agents |
| **Pipecat** | Modular voice pipeline (frames) | XTTS, Cartesia, ElevenLabs, OpenAI; özel TTS Frame processor yazılabilir | github.com/pipecat-ai/pipecat, docs.pipecat.ai/api-reference/server/services/tts/xtts |
| **RealtimeTTS** | Engine matrix (lokal + cloud) | Coqui (XTTS), Bark, Edge, GTTS, ElevenLabs, OpenAI; sub-second latency hedef | github.com/KoljaB/RealtimeTTS |
| **StreamSpeech** | All-in-one speech ASR+ST+S2S+TTS | Kendi modeliyle entegre, generic TTS adapter yok | github.com/ictnlp/StreamSpeech |

WebRTC latency hesabı (LiveKit blog "Voice Agent Architecture"): **TTS 100-200 ms + ağ 50-150 ms = TTFB hedefimiz ~300 ms** ulaşılabilir; Cartesia Sonic referansı 90 ms (cloud).

**Neeko mimarisi için entegrasyon önerim**:
- Pipecat **Frame processor** ile self-hosted TTS (Chatterbox veya CosyVoice 2) wrap edilir.
- LiveKit Cloud / self-hosted media server WebRTC audio frame transport.
- Edge cihaz (Waveshare RK35x): LiveKit room'a katılan client; mikrofon Pipecat'e PCM gönderir, TTS PCM'i WebRTC üzerinden çocuğa geri çalar.
- Single tek-track yerine **subscription model**: Karakter sesi sunucuda üretilir, edge sadece oynatıcı.

### 5.4 Edge Inference — ONNX, TensorRT, GGML, Quantization

- **ONNX Runtime**: TTS modelleri için en taşınabilir runtime. INT8 quant tipik 3-4x speedup, x86 + ARM + WebAssembly destek (onnxruntime.ai/docs/tutorials/iot-edge). Kokoro ONNX export'u resmi (huggingface.co/onnx-community/kokoro), Chatterbox Multilingual ONNX (huggingface.co/onnx-community/chatterbox-multilingual-ONNX) mevcut.
- **TensorRT**: NVIDIA-only, Jetson Orin / RK3588 GPU yok ama Jetson Nano / Orin için tek seçenek. FP16 + INT8 kombinasyonu standart.
- **CoreML**: Apple Silicon ekibi için (iOS companion app). Kokoro CoreML port'u mevcut, Chatterbox MLX port topluluk projesi.
- **GGML / GGUF**: LLM dünyasının kuantize formatı; TTS için henüz "first-class" değil. Bark-GGML community fork var ama kalite kaybı yüksek.

**4-8GB VRAM/RAM edge cihazda çalışabilenler** (kendi raporlu boyutlardan tahmin):

| Model | Edge çalışır mı? | Notu |
| --- | --- | --- |
| Kokoro-82M (ONNX INT8) | **Evet, CPU bile yeter** | RPi 4 üzerinde gerçek-zamanlı bildirilmiş |
| Piper (ONNX) | **Evet, CPU + 100 MB RAM yeter** | RPi 4 Zero üstü idealdir |
| MeloTTS | **Evet** (2-4 GB) | RPi 5 / mid-tier ARM |
| OpenVoice v2 base (küçük varyant) | **Sınırlı** (4-6 GB ile sıkışır) | Voice converter ek RAM tüketir |
| Chatterbox Turbo (ONNX, INT8) | **Sınırlı** (4-8 GB VRAM/RAM) | RK3588 NPU + ONNX Runtime ile fizibıl; CPU-only ağır |
| CosyVoice 2 | **Hayır** (8+ GB minimum) | Cloud only |
| Voxtral 4B / Higgs / Step-Audio | **Hayır** (12+ GB) | Cloud only |
| Sesame CSM-1B | **Sınırlı** (8 GB+ Apple MLX rapor edildi) | iOS companion app fizibıl |

> **Bize özel öneri (Alan 5)**:
>
> 1. **Cloud-first TTS, edge'de oynatıcı.** Neeko v1 cihazı PCM oynatıcı + mikrofon + WebRTC client. TTS sunucuda (Chatterbox veya CosyVoice 2). Bu kararı Neeko v2 humanoid'e kadar koru — donanım pahalılaşmasın diye TTS edge'e indirme.
> 2. **Orchestration: Pipecat + LiveKit kombosu.** Pipecat Frame'lerinde Chatterbox-as-service wrap'le; LiveKit WebRTC transport yap. Bu kombinasyon Neeko'nun ileride farklı TTS'lere geçişini ucuz tutar.
> 3. **TTFB hedefi 300 ms'e kilitlen.** Cartesia Sonic referansı 90 ms cloud, biz self-hosted Chatterbox/CosyVoice ile **300-500 ms gerçekçi**. ElevenLabs'in altına inmek **mümkün değil** kısa vadede; kalite + Türkçe önceliği için 500 ms kabul edilebilir.
> 4. **ONNX + INT8 quantization edge fallback path.** Eğer cloud kopukluğunda kısa Türkçe TTS lazım olursa Piper TR (ONNX) fallback olarak cihazda dursun. "İnternet yok, biraz sonra devam edeceğim" tipi minimal cümleler.
> 5. **Streaming-native mimariyi mimari kararı olarak yaz.** `00-research-brief.md` üzerine `02-architecture-decision.md` aç: "Neeko v1 TTS = Chatterbox Multilingual (POC sonucu olumluysa) veya CosyVoice 2 + TR fine-tune (B planı), self-hosted, Pipecat+LiveKit pipeline, edge fallback Piper TR." Bu karar drift'i önler.

---

## Bütünleyici Sonuç — Neeko v1 → v2 Aksiyon Hattı

1. **POC haftası (1 hafta)**: Chatterbox Multilingual Türkçe ile 20 cümlelik çocuk-karakter senaryosu üret. Voice prompt: Erdal'ın referans karakter sesi (5-10 sn). Kabul kriteri: Türkçe ı/ğ/ü/ö/ş/ç pronunciation doğru + prozod çocuk-dostu + TTFB <500 ms self-hosted GPU üzerinde.
2. **Fallback hazırlık (POC paralel)**: CosyVoice 2 fine-tune için Türkçe veri envanteri (Common Voice 17 TR ~80 saat, ek studio kayıt kapasitesi). Fine-tune POC açılırsa kim yapacak (kendi GPU vs cloud) kararı şimdiden.
3. **Lisans temizliği**: F5-TTS, MaskGCT, Voxtral, XTTS-v2 referansları repo'da test/comparison amaçlı kalsın ama **ticari ürün path'inden çıkar**. `neeko-voice/docs/decisions/` altına "non-commercial TTS modelleri kullanılmaz" kararı yaz.
4. **Stack kararı**: Pipecat + LiveKit + (Chatterbox/CosyVoice 2) + Piper-fallback. Cartesia + ElevenLabs cloud kalitesi karşılaştırma referansı olarak A/B'de tutulur ama production path açık-ağırlık.
5. **v2 humanoid bakışı**: Sesame CSM-1B gibi Apple MLX-friendly modeller v2'de daha güçlü cihaz olunca on-device fizibıl olabilir; şimdilik takipte.

**Tek cümleyle**: ElevenLabs'tan çıkış için **Chatterbox Multilingual** + self-host + LiveKit/Pipecat = en kısa, en az risk, en az fine-tune. Tek belirsizlik **Türkçe çocuk-prozodi kalitesi**, bu da POC ile bir haftada çözülür.

---

### Kaynaklar (özetlenmiş)

- TTS Arena V2 Leaderboard, `tts-agi-tts-arena-v2.hf.space/leaderboard` (Nis 2026)
- Artificial Analysis TTS leaderboard, `artificialanalysis.ai/text-to-speech/leaderboard` (Mayıs 2026)
- F5-TTS paper, `arxiv.org/abs/2410.06885` (Ekim 2024)
- CosyVoice 2 paper, `arxiv.org/pdf/2412.10117` (Aralık 2024)
- MaskGCT paper, `arxiv.org/pdf/2409.00750` (Eylül 2024)
- Step-Audio-EditX paper, `arxiv.org/abs/2511.03601` (Kasım 2025)
- VoXtream paper, `arxiv.org/html/2509.15969v1` (Eylül 2025)
- Mistral Voxtral TTS, `mistral.ai/news/voxtral-tts` (Mart 2026), `huggingface.co/mistralai/Voxtral-4B-TTS-2603`
- Fish Audio S2-Pro, `fish.audio/blog/fish-audio-open-sources-s2` (Mart 2026)
- Higgs Audio v2, `boson.ai/blog/higgs-audio-v2` (Temmuz 2025), `github.com/boson-ai/higgs-audio`
- Step-Audio-EditX, `github.com/stepfun-ai/Step-Audio-EditX` (Kasım 2025)
- Chatterbox Multilingual, `resemble.ai/introducing-chatterbox-multilingual-open-source-tts-for-23-languages`, `github.com/resemble-ai/chatterbox`
- Kokoro-82M, `huggingface.co/hexgrad/Kokoro-82M`, `github.com/remsky/Kokoro-FastAPI`
- F5-TTS-Turkish, `huggingface.co/marduk-ra/F5-TTS-Turkish` (Common Voice 17, 5.57k örnek, Ekim 2025)
- Piper TR voices, `rhasspy.github.io/piper-samples`, `github.com/rhasspy/piper`
- LiveKit Agents TTS docs, `docs.livekit.io/agents/models/tts/`
- Pipecat XTTS service, `docs.pipecat.ai/api-reference/server/services/tts/xtts`
- RealtimeTTS, `github.com/KoljaB/RealtimeTTS`
- Modal "1-Second Voice-to-Voice" blog, `modal.com/blog/low-latency-voice-bot`
- BentoML Open-Source TTS 2026 review, `bentoml.com/blog/exploring-the-world-of-open-source-text-to-speech-models`
- Baseten XTTS-v2 streaming, `baseten.co/blog/streaming-real-time-text-to-speech-with-xtts-v2`
- ONNX Runtime INT8 quantization, `opensource.microsoft.com/blog/2022/05/02/optimizing-and-deploying-transformer-int8-inference-with-onnx-runtime-tensorrt`
- Sesame CSM-1B, `huggingface.co/sesame/csm-1b` (Mart 2025)
- Coqui XTTS-v2 lisans tartışması, `github.com/coqui-ai/TTS/discussions/4304`
- TurkicTTS (Azerice/Kazakça/Türkçe vb. 10 Turkic dil), `github.com/IS2AI/TurkicTTS` (referans amaçlı, başka bağlam)
