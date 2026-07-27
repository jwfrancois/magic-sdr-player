// AI helper for Magic SDR Player.
//
// Invoked by Python's ai_tagger.py via subprocess. Takes a JSON object on
// stdin describing a signal and returns a JSON classification on stdout.
//
// Input JSON shape:
//   {
//     "frequency_hz": 96900000,
//     "modulation": "WFM_ST",
//     "band": "FM Broadcast",
//     "signal_level_db": -32.5,
//     "audio_features": {
//       "spectral_centroid_hz": 4200.0,
//       "zero_crossing_rate": 0.18,
//       "rms": 0.42,
//       "dominant_freq_hz": 220.0,
//       "bandwidth_hz": 15000
//     },
//     "duration_s": 5.0,
//     "known_label": "NOAA WX-1 (162.400)"
//   }
//
// Output JSON shape (printed to stdout):
//   {
//     "signal_type": "music|talk|weather|aviation|ham|marine|noise|data|unknown",
//     "language": "en|es|zh|...|n/a",
//     "summary": "Short human-readable description of what's likely on the channel."
//   }

import ZAI from 'z-ai-web-dev-sdk';
import { readFileSync } from 'fs';

async function classifySignal() {
  // Read JSON input from stdin
  const raw = readFileSync(0, 'utf-8');
  let input;
  try {
    input = JSON.parse(raw);
  } catch (e) {
    console.log(JSON.stringify({
      signal_type: 'unknown',
      language: 'n/a',
      summary: 'Could not parse input features — defaulting to unknown.'
    }));
    return;
  }

  const fMHz = (input.frequency_hz / 1e6).toFixed(3);
  const knownLabel = input.known_label || 'unlabeled';
  const feats = input.audio_features || {};

  const systemPrompt = `You are a radio signal classifier. Given the frequency, modulation, band, signal strength, and audio features of an unknown radio transmission, classify what is most likely on the channel.

Respond ONLY with valid JSON, no markdown, no preamble. Schema:
{
  "signal_type": one of [music, talk, weather, aviation, ham, marine, noise, data, unknown],
  "language": a 2-letter ISO code or "n/a",
  "summary": one short sentence (<=120 chars) describing likely content
}

Use the audio features to discriminate music vs talk vs noise vs data. Spectral centroid > 4000 Hz with high RMS often suggests music; low centroid with variable zero-crossing suggests speech; very narrow bandwidth with stable dominant freq suggests data or tone.`;

  const userPrompt = `Frequency: ${fMHz} MHz
Modulation: ${input.modulation}
Band: ${input.band}
Signal level: ${input.signal_level_db} dB
Known channel label: ${knownLabel}

Audio features (${input.duration_s || 5}s sample):
- Spectral centroid: ${feats.spectral_centroid_hz || 'n/a'} Hz
- Zero-crossing rate: ${feats.zero_crossing_rate || 'n/a'}
- RMS amplitude: ${feats.rms || 'n/a'}
- Dominant frequency: ${feats.dominant_freq_hz || 'n/a'} Hz
- Bandwidth: ${feats.bandwidth_hz || 'n/a'} Hz

Classify this signal.`;

  try {
    const zai = await ZAI.create();
    const completion = await zai.chat.completions.create({
      messages: [
        { role: 'assistant', content: systemPrompt },
        { role: 'user', content: userPrompt }
      ],
      thinking: { type: 'disabled' }
    });

    let content = completion.choices[0]?.message?.content || '';
    // Strip markdown fences if present
    content = content.replace(/```json\s*/g, '').replace(/```/g, '').trim();
    // Find the first { ... } block
    const start = content.indexOf('{');
    const end = content.lastIndexOf('}');
    if (start >= 0 && end > start) {
      content = content.slice(start, end + 1);
    }
    try {
      const parsed = JSON.parse(content);
      console.log(JSON.stringify(parsed));
      return;
    } catch (e) {
      console.log(JSON.stringify({
        signal_type: 'unknown',
        language: 'n/a',
        summary: content.slice(0, 200)
      }));
    }
  } catch (err) {
    console.log(JSON.stringify({
      signal_type: 'unknown',
      language: 'n/a',
      summary: `AI classifier unavailable: ${err.message}`
    }));
  }
}

classifySignal().catch(err => {
  console.log(JSON.stringify({
    signal_type: 'unknown',
    language: 'n/a',
    summary: `AI helper failed: ${err.message}`
  }));
});
