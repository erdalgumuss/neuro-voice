# Ultra Low Latency Voice/TTS Strateji Notu

**Tarih:** 2026-05-24  
**Hedef:** 100 ms, hatta 80 ms hedefini ciddiye alarak, repo bağımsız olarak hangi geliştirme stratejileri gerekir?  
**Kapsam:** TTS, streaming TTS, voice chat, voice agent, transport, model runtime, altyapı, ölçüm ve ürün algısı.  
**Durum:** Araştırma ve mühendislik strateji dokümanı. Mimari karar değildir.

---

## 0. Yönetici Özeti

80-100 ms hedefi mümkündür ama yalnızca doğru tanımla mümkündür. Bu hedef, genellikle **TTS modelinin ilk audio packet/first byte üretmesi** veya **hazır text girdisinden ilk oynatılabilir audio chunk'a ulaşması** için konuşulabilir. Bir kullanıcının konuşmayı bitirmesi, sistemin anlaması, LLM'in düşünmesi, TTS'in konuşması ve istemcide sesin duyulması dahil **tam conversational turn** için 80-100 ms gerçekçi değildir; bu aralık için sistemin kullanıcı bitirmeden düşünmeye başlaması, cevap tahmin etmesi, persistent media bağlantısı kullanması ve streaming-native model çalıştırması gerekir.

Bu yüzden hedefi iki parçaya ayırmak gerekir:

| Hedef | Anlamı | 80-100 ms gerçekçiliği |
|---|---|---|
| Model FPL/TTFB | TTS modelinin ilk audio packet/byte üretmesi | Streaming-native model + GPU optimize runtime ile mümkün |
| Service TTFA | Gateway request aldı -> ilk audio chunk çıktı | Çok zor ama co-located, warm, no queue, WS/WebRTC ile yaklaşılabilir |
| Client first audible | Kullanıcı başlattı -> cihazda ilk ses duyuldu | 80-100 ms çok agresif; iyi ağ + preconnected media + küçük jitter buffer gerekir |
| Full turn latency | Kullanıcı konuşmayı bitirdi -> agent ilk ses verdi | 300-600 ms iyi, 600 ms-1.2 s yaygın; 100 ms ancak predictive/full-duplex ile algısal olarak yaklaşır |

Ana ders: **100 ms hedefi bir model benchmark'ı değil, bütün sistemin "hiçbir şeyi beklememe" disipliniyle tasarlanmasıdır.**

---

## 1. Latency Sözlüğü

Bu metrikleri karıştırırsak yanlış şeyi optimize ederiz.

| Metrik | Tanım | Neyi ölçer? |
|---|---|---|
| FPL | First Packet Latency | Modelin ilk audio packet üretmesi |
| TTFB | Time To First Byte | Servisten ilk byte çıkması |
| TTFA | Time To First Audio | İlk oynatılabilir audio chunk'ın istemciye ulaşması |
| First audible | Kullanıcının cihazında ilk sesin gerçekten duyulması |
| TTFT | LLM'in ilk token üretme süresi |
| End-of-utterance delay | Kullanıcının bitirdiğini anlamak için beklenen süre |
| Barge-in latency | Kullanıcı araya girince agent'ın konuşmayı durdurma süresi |
| E2E turn latency | Kullanıcı turn'ü bitti -> agent ilk sesi duyuldu |
| p95/p99 tail latency | Sistemin kötü anlarda ne kadar geciktiği |

80-100 ms hedefi için **p50 yetmez**. Voice üründe p95 çok önemlidir. İnsan tek bir güzel demo değil, konuşmanın ritmini hisseder.

---

## 2. Sektör Sinyali

Kaynaklardan çıkan yön:

- ElevenLabs latency dokümanı hızlı model, streaming, coğrafi yakınlık ve doğru voice seçimini ana prensip olarak verir. Flash modeller için yaklaşık 75 ms inference hızından bahseder ama bunun sadece model inference süresi olduğunu, gerçek end-to-end latency'nin endpoint, bölge ve bağlantıya bağlı olduğunu belirtir. US WebSocket + Flash için 150-200 ms TTFB, EU için yaklaşık 230 ms gibi değerler paylaşır.
- Cartesia, tüm TTS endpoint'lerinin audio'yu üretildikçe stream ettiğini, WebSocket'in uzun oturum ve partial transcript için doğru yol olduğunu, yeni HTTPS isteğinin her turda TCP/TLS maliyeti ödettiğini ve bunun audio TTFB ile aynı büyüklükte olabileceğini söyler. WebSocket'te `context_id`, continuations, timestamps ve tek bağlantıda çok generation pattern'i var.
- Deepgram self-hosted metrikleri TTS first raw byte ve first transcoded byte için histogram bucket'ları tutar. Bu, sektörün latency'yi tek sayaç değil, first-byte katmanlarıyla ölçtüğünü gösterir.
- OpenAI Realtime docs, client media için WebRTC'nin WebSocket'e göre daha robust olduğunu; WebSocket'te audio buffer ve base64 event yönetiminin manuel kaldığını belirtir.
- LiveKit voice agent yazıları, pipeline ile realtime model ayrımını net yapar: cascaded STT -> LLM -> TTS pipeline daha kontrol edilebilir; speech-to-speech/realtime path daha düşük latency verir ama debug ve tool-calling kontrolü azalır. Streaming'in her stage boundary'de yapılması "2 saniye" ile "400 ms" arasındaki fark olarak anlatılır.
- VoXtream paper'ı streaming-native TTS tarafında 102 ms GPU first-packet latency raporlar. Bunu tam metni beklemeden ilk kelimeden konuşmaya başlayan, incremental phoneme transformer, monotonic alignment ve dynamic look-ahead ile yapar.
- 2026 block-wise codec decoding paper'ı, neural vocoder bottleneck'ini kaldırıp Mimi codec latent space üstünden block-wise üretimle ortalama 48.99 ms first-byte latency raporlar. Bu bir research sinyalidir: 50-100 ms sınıfı, çoğunlukla klasik offline TTS pipeline ile değil, streaming-native codec/token mimarisiyle gelir.

Bu tablo bize şunu söyler: 100 ms hedefi için sadece "daha güçlü GPU" değil, **transport + model mimarisi + runtime + prewarm + streaming protocol** birlikte gerekir.

---

## 3. 80-100 ms Hedefi İçin Gerçekçi Latency Budget

### 3.1 TTS-only, hazır text, preconnected session

Bu en ulaşılabilir 100 ms hedefidir.

| Segment | İdeal bütçe |
|---|---:|
| Client -> edge/gateway frame | 5-15 ms |
| Gateway routing/auth/session lookup | 1-5 ms |
| Worker dispatch, no queue | 1-5 ms |
| Text normalization/incremental phoneme | 1-5 ms |
| Model first packet | 40-80 ms |
| First packet serialize/send | 5-15 ms |
| Client jitter/playback start | 10-30 ms |
| **Toplam** | **63-155 ms** |

Bu ancak şunlarla olur:

- persistent WebSocket/WebRTC,
- warm model,
- warm adapter/voice profile,
- no cold start,
- no R2/reference fetch,
- no queue wait,
- streaming-native TTS,
- client küçük jitter buffer,
- aynı region.

### 3.2 Full chat turn, kullanıcı konuşuyor

Burada 80-100 ms ancak "kullanıcı bittikten sonra" değil, **kullanıcı konuşurken düşünmeye başlayarak** yaklaşılabilir.

| Segment | Agresif hedef |
|---|---:|
| VAD/turn signal | 20-80 ms |
| Partial STT veya audio-native understanding | sürekli |
| LLM semantic trigger / first token | 30-150 ms |
| TTS first audio | 80-200 ms |
| Client playback | 20-50 ms |
| **Algısal ilk cevap** | **200-500 ms** |

100 ms full-turn için gerekenler:

- full-duplex listening,
- partial ASR,
- semantic turn prediction,
- LLM'in kullanıcı bitmeden state hazırlaması,
- cevap başlangıcının tahmin edilmesi,
- interrupt/cancel mekanizması,
- bazen filler/backchannel audio.

---

## 4. Strateji Kataloğu

### 4.1 Ölçüm ve Observability Önce Gelir

100 ms hedefi ölçmeden geliştirilemez. Her stage için timestamp gerekir:

```text
client_input_started_at
client_input_committed_at
server_received_at
auth_done_at
session_ready_at
queue_entered_at
worker_claimed_at
reference_ready_at
adapter_ready_at
normalization_done_at
model_start_at
model_first_packet_at
first_packet_sent_at
gateway_first_packet_at
client_first_packet_at
client_first_audible_at
final_audio_done_at
```

Önerilen metrikler:

- `tts_model_first_packet_ms`
- `tts_service_ttfa_ms`
- `client_first_audible_ms`
- `queue_wait_ms`
- `adapter_cache_hit`
- `reference_cache_hit`
- `transport_connection_reused`
- `jitter_buffer_ms`
- `barge_in_stop_ms`
- `e2e_turn_latency_ms`
- p50, p90, p95, p99 ayrı.

Kural:

```text
p50 demo içindir.
p95 ürün içindir.
p99 kriz içindir.
```

### 4.2 Persistent Transport

100 ms hedefinde her turn için yeni HTTPS request açmak pahalıdır.

Yapılması gerekenler:

- WebSocket veya WebRTC session önceden açılır.
- Her konuşma turn'ü aynı connection üzerinden akar.
- Session handshake kullanıcı konuşmadan önce yapılır.
- TLS/TCP maliyeti turn path'inden çıkarılır.
- Audio chunks binary taşınır; JSON + base64 sadece control plane için kullanılır.
- HTTP streaming yalnız "full text hazır, latency orta hedef" için tutulur.

Alternatifler:

| Transport | Artı | Eksi | Kullanım |
|---|---|---|---|
| HTTP bytes streaming | Basit, raw bytes ucuz | Her turn request maliyeti | Batch/notification |
| SSE | Metadata kolay | JSON/base64 overhead, one-way | Browser uyumluluk |
| WebSocket | Long-lived, bidirectional, context | Media jitter yönetimi bizde | TTS live session |
| WebRTC | Media path, jitter, NAT, audio track doğal | Daha karmaşık signaling | Voice chat/agent |
| SIP/RTP | Telefon dünyası | Telco jitter/transcoding | Call center |

80-100 ms hedefinde ana yol **WebRTC veya preconnected WebSocket** olmalı.

### 4.3 Region ve Co-location

Network hedefi kabaca:

```text
client <-> edge: düşük RTT
edge <-> model: aynı region / aynı AZ
model <-> storage/cache: aynı region
```

Yapılması gerekenler:

- Kullanıcı Türkiye ise primary EU region.
- Gateway, Redis, GPU worker, model cache, object storage aynı bölgeye yakın.
- Provider API kullanılıyorsa provider region seçimi yapılır.
- Multi-provider pipeline'da STT, LLM, TTS farklı kıtalarda olmamalı.
- Telefon/PSTN varsa medya edge nerede, agent server nerede açıkça ölçülür.

Not: 80 ms hedefinde kıtalar arası hop yoktur. Fizik izin vermez.

### 4.4 Queue ve Scheduling

Canlı voice path ile batch job path ayrılmalı.

80-100 ms path:

- Queue wait yok veya mikro-scheduler var.
- Priority lane.
- Dedicated warm worker.
- One active live stream per GPU slice veya modelin gerçek concurrency kapasitesine göre admission control.
- Backpressure erken verilir; request kuyrukta bekletilmez.

Batch path:

- Redis/Kafka/SQS queue olabilir.
- R2 artifact, retries, idempotency, DLQ olabilir.
- Latency değil throughput optimize edilir.

Kural:

```text
Canlı konuşma path'i kuyrukta beklemez.
Bekleyecekse kullanıcıya "connecting/thinking" sinyali verilir veya fallback'e düşer.
```

### 4.5 Model Mimari Seçimi

80-100 ms sınıfı için klasik offline TTS yeterli olmayabilir.

Aranacak model özellikleri:

- Incremental input: tam cümleyi beklemeden başlayabilir.
- Output streaming: tüm audio bitmeden packet verebilir.
- Causal veya limited look-ahead.
- Monotonic alignment.
- Low frame-rate codec tokens.
- Block-wise generation.
- Fast vocoder veya vocoder'sız codec decode.
- Speaker embedding önceden hesaplanabilir.
- Style/prosody kısa prefix ile kurulabilir.
- Compile/optimized runtime destekler.

Model stratejileri:

| Strateji | Ne sağlar? | Risk |
|---|---|---|
| Flash/small TTS model | Düşük FPL | Kalite/duygu düşebilir |
| Streaming-native TTS | İlk kelime/phoneme ile başlama | Model seçeneği az |
| Codec-token TTS | Düşük frame rate, hızlı decode | Artifact/kalite riski |
| Distilled character model | Belirli ses için hızlı | Çok sesli ölçek zorluğu |
| Adapter/LoRA cache | Karakter tutarlılığı | Cache miss latency |
| Edge/on-device TTS | Network latency düşer | Kalite/cihaz kapasitesi |
| Vendor fallback | Hızlı ürünleşme | Maliyet/bağımlılık |

### 4.6 Runtime Optimizasyonu

GPU güçlü diye 100 ms gelmez. Runtime ayrı optimize edilir.

Yapılacaklar:

- Model warmup.
- `torch.compile`, TensorRT, ONNX Runtime, CUDA Graphs gibi path'ler denenir.
- FP16/BF16, INT8/INT4 quantization benchmark edilir.
- Decoder/vocoder ayrı profillenir.
- CPU preprocessing GPU path'i bekletmez.
- Memory allocation sabitlenir; request başına büyük allocation yapılmaz.
- Adapter ve speaker embedding cache'i hazır tutulur.
- Chunk producer ve network sender farklı coroutine/thread ile akar.
- Batching canlı path'te dikkatli kullanılır; batch throughput artırır ama TTFA'yı bozabilir.

Önemli trade-off:

```text
Throughput batching ile gelir.
80-100 ms latency çoğu zaman batching'i sınırlar.
```

### 4.7 Audio Format ve Client Playback

İlk audio chunk'ın hızlı gelmesi yetmez; oynatıcı beklerse kullanıcı duyamaz.

Yapılması gerekenler:

- Raw PCM veya Opus gibi streaming-friendly format.
- Büyük WAV header bekleme yok.
- Küçük frame size: 10-20 ms audio frames.
- Client jitter buffer küçük ama güvenli: 20-60 ms.
- Playback graph önceden hazırlanır.
- Browser autoplay/user gesture kısıtları çözülür.
- AudioContext önceden resume edilir.
- İlk chunk decode path'i benchmark edilir.

Format trade-off:

| Format | Latency | Bandwidth | Not |
|---|---:|---:|---|
| PCM | Çok düşük decode | Yüksek | En basit, LAN/WS için iyi |
| Opus | Düşük, WebRTC doğal | Düşük | Voice chat için güçlü |
| MP3 | Decode/packet gecikmesi olabilir | Orta | Low-latency için ilk tercih değil |
| WAV | Container kolay | Header/streaming dikkat ister | Batch ve simple HTTP için iyi |

### 4.8 Text Chunking ve Prosody

TTS'e çok küçük text vermek hızlıdır ama kötü prozodi üretir. Çok büyük text vermek doğal olabilir ama geç başlar.

Kullanılan patternler:

- punctuation-aware chunking,
- phrase-level chunking,
- dynamic look-ahead,
- chunk schedule,
- continuation context,
- first phrase fast path,
- later chunks quality path.

İdeal davranış:

```text
İlk 2-5 kelimeyle konuşmaya başla.
Sonraki kelimeler geldikçe prosody'yi koru.
Eğer LLM yön değiştirirse context'i cancel/flush et.
```

Bu yüzden WebSocket context/continuation pattern'i önemlidir.

### 4.9 LLM ve Dialogue Katmanı

Full chat latency'de TTS tek başına yetmez.

LLM tarafında:

- küçük/fast model fast path,
- intent-classifier short-circuit,
- prompt kısa tutulur,
- konuşma geçmişi özetlenir,
- tool/RAG prefetch yapılır,
- tool calls paralelleştirilir,
- first token hedefi ayrı ölçülür,
- cevap streaming başlar başlamaz TTS tetiklenir,
- güvenli cevap prefixleri cache'lenir.

Extreme strategy:

- Kullanıcı konuşurken partial transcript üstünden LLM context hazırlanır.
- Semantic trigger gelince cevap başlatılır.
- Eğer son kelimeler anlamı değiştirirse response cancel edilir.

Bu insan konuşmasına daha yakındır: dinlerken düşünmeye başlamak.

### 4.10 STT, VAD ve Turn Detection

100 ms chat hedefinde en büyük düşman bazen TTS değil, "kullanıcı bitti mi?" sorusudur.

Stratejiler:

- VAD sürekli çalışır.
- Endpointing delay agresif ayarlanır ama kelime kesmeyecek kadar güvenli tutulur.
- Semantic turn detector VAD üstüne eklenir.
- Partial STT kullanılır.
- Barge-in her zaman açık olur.
- Kullanıcı konuşunca TTS playback hemen stop/flush edilir.
- Echo cancellation ve noise suppression turn detection'ı destekler.
- Push-to-talk modunda latency çok düşer, ama UX değişir.

Trade-off:

```text
Daha agresif endpointing = daha hızlı cevap, daha fazla kesme riski.
Daha konservatif endpointing = daha doğal anlama, daha fazla bekleme.
```

### 4.11 Speculative ve Predictive Audio

80-100 ms hissi bazen gerçek hesaplamadan değil, doğru tahminden gelir.

Patternler:

- cached greetings,
- earcons / tiny acknowledgement clips,
- "hmm", "tamam", "anladım" gibi backchannel audio,
- first syllable/short prefix pre-generation,
- predicted response prefix,
- response cancel + truncate,
- semantic templates.

Risk:

- Yanlış tahmin güveni bozar.
- Çocuk ürünü veya regüle alanlarda filler aşırı kullanılmamalı.
- Filler "düşünüyor" hissi verebilir ama yanlış bilgi vermemeli.

### 4.12 Fallback ve Degradation

Ultra-low latency sistemde fallback şarttır.

Fallback katmanları:

- fast low-quality TTS -> premium TTS'e göre daha hızlı.
- local/edge simple voice -> cloud gecikirse.
- pre-recorded safe clips -> TTS fail olursa.
- vendor fallback -> self-host worker overload olursa.
- text response fallback -> voice path kırılırsa.

Kural:

```text
Sessizlik en kötü fallback'tir.
```

---

## 5. Mimari Alternatifler

### Alternatif A - Cascaded Pipeline

```text
VAD -> STT -> LLM -> TTS -> playback
```

Artı:

- Debug kolay.
- Her parça değiştirilebilir.
- Tool calling ve compliance güçlü.
- Text transcript net.

Eksi:

- Her hop latency ekler.
- Prosody ve emotion STT'de kaybolabilir.
- 80-100 ms full turn hedefi çok zor.

Ne zaman:

- Ürün güvenilirliği, tool calls, audit ve domain doğruluğu önemliyse.

### Alternatif B - Realtime Speech-to-Speech

```text
audio in -> realtime multimodal model -> audio out
```

Artı:

- Daha az stage.
- Daha doğal turn-taking.
- Emotion/prosody korunur.
- Sub-300 ms hissi daha olası.

Eksi:

- Debug zor.
- Tool calling daha sınırlı/karmaşık olabilir.
- Provider lock-in.
- Ses kimliği/özel voice kontrolü sınırlı olabilir.

Ne zaman:

- Latency en büyük ürün farkıysa.
- Kısa, sosyal, serbest sohbet önemliyse.

### Alternatif C - Hybrid

```text
Fast realtime path: kısa cevaplar
Cascaded path: tool, bilgi, doğrulama, kayıt
```

Artı:

- Hız ve kontrol dengesi.
- Basit sohbet hızlı, kritik işlem güvenli.

Eksi:

- Orchestration zor.
- İki kalite modu arasında geçiş hissedilebilir.

Bu, 2026 için en makul premium ürün yönüdür.

### Alternatif D - Edge/On-device

```text
cihaz/edge TTS + cloud LLM veya local small model
```

Artı:

- Network düşer.
- İlk ses çok hızlı olabilir.
- Offline/oyuncak senaryosunda güçlü.

Eksi:

- Kalite sınırlı.
- Model update ve cihaz kapasitesi sorun.
- Voice cloning/adapters sınırlı.

Ne zaman:

- Oyuncak, kiosk, offline mode, fallback.

---

## 6. Development Yol Haritası

### Faz 1 - Ölç

- Timestamp zinciri.
- Client RUM.
- Synthetic benchmark.
- p50/p95/p99 dashboards.
- Network RTT ölçümü.
- Model FPL benchmark.
- First audible ölçümü.

Çıkış kriteri:

```text
Her request için latency waterfall görülebiliyor.
```

### Faz 2 - Streaming Gerçekliği

- `list(generate_stream())` gibi tüm çıktıyı bufferlayan path'ler kaldırılır.
- İlk audio chunk üretildiği anda network'e verilir.
- WS/WebRTC preconnect.
- Client playback hazır tutulur.
- Archive/DB/finalization first audio path'inden çıkarılır.

Çıkış kriteri:

```text
Model ilk chunk üretir üretmez client playback path'ine giriyor.
```

### Faz 3 - Warm Path

- Model warm.
- Adapter warm.
- Speaker embedding warm.
- Reference cache warm.
- No cold starts.
- Dedicated live workers.

Çıkış kriteri:

```text
Cache hit live path p95 sabit.
```

### Faz 4 - Runtime/Model Benchmark

- Mevcut model direct runtime.
- Compile/runtime optimize.
- Flash/small TTS alternatifi.
- Streaming-native TTS alternatifi.
- Vendor benchmark.
- Edge/simple fallback.

Çıkış kriteri:

```text
Model FPL p50/p95 tablo halinde biliniyor.
```

### Faz 5 - Voice Agent Optimization

- VAD/endpointing tuning.
- Partial STT.
- LLM first token optimize.
- Tool prefetch.
- Semantic triggers.
- Barge-in.
- Response cancel/truncate.

Çıkış kriteri:

```text
Full turn p50 < 600 ms, p95 < 1.2 s hedeflenebilir.
```

### Faz 6 - 80-100 ms Moonshot

- Streaming-native TTS seçimi veya eğitimi.
- First-word/phoneme generation.
- Low-frame-rate codec.
- WebRTC media path.
- Co-located GPU edge.
- No queue live path.
- Predictive first audio.
- On-device/edge fallback.

Çıkış kriteri:

```text
TTS-only preconnected warm path first packet p50 80-120 ms.
```

---

## 7. Hangi Şeyleri Yapmamalı?

- Her turn için yeni HTTPS request.
- İlk audio'dan önce R2 archive beklemek.
- İlk audio'dan önce DB final commit beklemek.
- Canlı voice path'ini batch queue ile aynı hatta sokmak.
- Base64 JSON audio chunk'larıyla ultra-low latency hedeflemek.
- Cold model/adapter/reference load'u request path'inde yapmak.
- Sadece p50 bakmak.
- VAD'ı çok agresif yapıp kullanıcı sözünü kesmek.
- Tüm cümleyi bekleyip sonra TTS başlatmak.
- Tüm audio'yu üretip sonra stream etmek.
- Tool call beklerken sessiz kalmak.
- Fallback'siz premium path tasarlamak.

---

## 8. 80-100 ms İçin Hedef Mimari Prensipler

1. **Session önce açılır, konuşma sonra başlar.**
2. **Media transport kalıcıdır.**
3. **Canlı path queue beklemez.**
4. **Worker sıcak kalır.**
5. **Voice assets sıcak kalır.**
6. **TTS streaming-native olmalıdır.**
7. **İlk audio için finalization beklenmez.**
8. **Client playback graph hazırdır.**
9. **LLM text'i gelmeye başlar başlamaz TTS başlar.**
10. **Kullanıcı araya girince her şey cancel/flush olur.**
11. **Her stage ölçülür.**
12. **Fallback sessizlikten önce gelir.**

---

## 9. Research Backlog

80-100 ms hedefi için araştırılması gereken başlıklar:

- Streaming-native open TTS: VoXtream, VoXtream2, CosyVoice2 streaming, Qwen/TTS ultra-low-latency iddiaları.
- Neural codec path: Mimi, EnCodec, low frame-rate codec token generation.
- Runtime: torch.compile, TensorRT, CUDA Graphs, ONNX Runtime, Triton kernels.
- WebRTC media server: LiveKit, custom SFU, direct peer connection, SIP bridge.
- Client playback: Web Audio API, AudioWorklet, jitter buffer, Opus decode.
- Voice identity: speaker embedding precompute, adapter hot swap, instant clone latency.
- Speculative speech: cancellable TTS, prefix generation, backchannel clips.
- Full-duplex dialogue: VAD-free cascaded, semantic triggers, listen-think-speak.

---

## 10. Kaynaklar

- [ElevenLabs - Latency optimization](https://elevenlabs.io/docs/api-reference/reducing-latency) - Flash model, streaming/WebSocket, coğrafi yakınlık, voice seçimi ve TTFB bölge sinyalleri.
- [Cartesia - Compare TTS endpoints](https://docs.cartesia.ai/use-the-api/compare-tts-endpoints) - bytes/SSE/WebSocket farkı, TCP/TLS maliyeti, WebSocket continuations ve live session pattern'i.
- [Cartesia - TTS WebSocket](https://docs.cartesia.ai/api-reference/tts/websocket) - context_id, multiplexing, pre-opened WebSocket, chunk response ve timestamp events.
- [Deepgram - Metrics guide](https://developers.deepgram.com/docs/metrics-guide) - TTS first raw byte ve first transcoded byte latency histogramları.
- [OpenAI - Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations) - WebRTC/WebSocket realtime session, audio buffer, semantic/server VAD ve WebRTC media output önerisi.
- [LiveKit - Turns overview](https://docs.livekit.io/agents/logic/turns/) - VAD, endpointing, turn detection, interruptions, adaptive interruption handling.
- [LiveKit - Understand and Improve Voice Agent Latency](https://livekit.com/blog/understand-and-improve-agent-latency) - stage-level latency, region co-location, realtime vs pipeline, VAD prewarm, observability.
- [LiveKit - Sequential Pipeline Architecture](https://livekit.com/blog/sequential-pipeline-architecture-voice-agents) - VAD -> STT -> LLM -> TTS pipeline, barge-in, cascaded vs speech-to-speech, streaming boundaries.
- [VoXtream: Full-Stream Text-to-Speech with Extremely Low Latency](https://arxiv.org/abs/2509.15969) - first-word streaming TTS, 102 ms GPU first-packet latency.
- [Ultra-Low Latency Streaming Speech Synthesis via Block-Wise Generation and Depth-Wise Codec Decoding](https://arxiv.org/abs/2604.12438) - neural codec latent space ve 48.99 ms average first-byte latency research sonucu.

---

## 11. Sonuç

100 ms hedefi için yaklaşım şu olmalı:

```text
Önce ölç.
Sonra tüm stage'leri stream et.
Sonra cold path'i yok et.
Sonra model/runtime seç.
Sonra media transport'u WebRTC/WS seviyesinde optimize et.
Sonra predictive/full-duplex davranış ekle.
```

80-100 ms hedefi tek başına "daha hızlı GPU" işi değildir. Bu hedef, product UX'ten media transport'a, model mimarisinden runtime'a, client playback'ten observability'ye kadar bütün ses sisteminin latency-first tasarlanmasıdır.

En dürüst hedef ayrımı:

```text
TTS-only first packet: 80-120 ms moonshot.
TTS service TTFA: 150-300 ms güçlü hedef.
Full voice-agent turn: 300-600 ms çok iyi hedef.
Full production p95: 600 ms-1.2 s kabul edilebilir iyi ürün aralığı.
```

80 ms istiyorsak buna göre ayrı bir live path kuracağız. Batch API, artifact storage, async jobs ve billing path'i ayrı kalacak; canlı konuşma yolu ise sıcak, kısa, ölçülü ve streaming-native olacak.
