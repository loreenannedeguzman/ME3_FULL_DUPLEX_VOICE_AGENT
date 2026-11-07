#include <cstdint>
#include <new>
#include <memory>
#include "modules/audio_processing/include/audio_processing.h"

struct State {
  std::unique_ptr<webrtc::AudioProcessing> apm;
  webrtc::StreamConfig mono{16000, 1};
};
extern "C" {
int me3_apm_abi() noexcept { return 1; }
int me3_apm_profile() noexcept { return 0x101; }
void* me3_apm_new() noexcept {
  try {
    auto s = std::make_unique<State>();
    s->apm.reset(webrtc::AudioProcessingBuilder().Create());
    if (!s->apm) return nullptr;
    webrtc::AudioProcessing::Config c;
    c.echo_canceller.enabled = true;
    c.echo_canceller.mobile_mode = false;
    c.echo_canceller.enforce_high_pass_filtering = false;
    c.high_pass_filter.enabled = false;
    c.noise_suppression.enabled = true;
    c.noise_suppression.level = webrtc::AudioProcessing::Config::NoiseSuppression::kLow;
    c.gain_controller1.enabled = false;
    c.gain_controller2.enabled = false;
    c.pre_amplifier.enabled = false;
    c.transient_suppression.enabled = false;
    c.voice_detection.enabled = false;
    s->apm->ApplyConfig(c);
    webrtc::ProcessingConfig f;
    f.input_stream() = f.output_stream() = f.reverse_input_stream() = f.reverse_output_stream() = s->mono;
    if (s->apm->Initialize(f) != 0) return nullptr;
    s->apm->set_output_will_be_muted(false);
    return s.release();
  } catch (...) { return nullptr; }
}
// Exactly20ms mono PCM16. Native APM requires10ms, split without resampling.
int me3_apm_process(void* ptr, const int16_t* mic, const int16_t* far,
                    int16_t* out, int samples) noexcept {
  if (!ptr || !mic || !far || !out || samples != 320) return -100;
  try {
    auto s = static_cast<State*>(ptr);
    int16_t reverse[160];
    for (int off = 0; off < 320; off += 160) {
      int status = s->apm->ProcessReverseStream(far + off, s->mono, s->mono, reverse);
      if (status != 0) return status;
      status = s->apm->set_stream_delay_ms(0);
      if (status != 0) return status;
      status = s->apm->ProcessStream(mic + off, s->mono, s->mono, out + off);
      if (status != 0) return status;
    }
    return 0;
  } catch (...) { return -101; }
}
void me3_apm_free(void* ptr) noexcept { try { delete static_cast<State*>(ptr); } catch (...) {} }
}
