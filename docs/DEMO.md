# Demo script: no i am not brainwashed (3 to 4 minutes)

Live URL: https://iamnotbrainwashed.tech (the old https://srv2160832.hstgr.cloud still works; see `docs/STATUS.md`). Numbers below are quoted from
`backend/eval/results/latest.json` (test set `eval_v3`, prompt v4; see `run_at` in the file). If a
newer eval run is published before the demo, re-read the page and update this file: never quote a
number the page does not show.

## The story in one sentence

A newspaper-style game where you hunt for propaganda techniques in three real headlines about one
event, and the same classifier that scores your answer is measured in the open, with its
mistakes, sample sizes and false-positive rate on display.

## The six steps

### 1. Open the board (about 20 s)

- Open the public URL. The page is "The Board": today's event, three clippings from different outlets,
  and a "Our track record" note card in the corner.
- Say: "Three outlets, one event. Your job is to name the technique, or say there is none, and mark the
  words that give it away. The reference answer comes from the same classifier you can inspect on the
  Track record page."
- Radio music is off by default and stays off.

### 2. Play one clipping, with a deliberate miss (about 40 s)

- Press "Examine clipping 1". Mark one or two words, then pick a technique tag on purpose that you
  think is wrong, or mark the wrong words. Press "File the report".
- Say: "I will get this one wrong on purpose, because the interesting part is what happens next."
- Score formula, shown on screen: 60 points for the right technique plus 40 times the token-F1 of the
  words you marked.

### 3. The reveal (about 30 s)

- Show the stamp about your call, the reference technique, the reference words next to yours, the
  explanation, and the confidence label (High, Moderate or Disputed).
- Say: "The reference answer is the system's call and it can be wrong. Confidence is how strongly the
  models agree with each other, not a probability."
- Show "I disagree: dispute this call". Say: "One dispute changes nothing. Three different players, or a
  team decision, start a review. Confirmed disputes go to the development set only, never to the
  frozen test set."

### 4. /metrics walk-through (about 70 s)

Open "Track record". Say that three sources are shown, separately, and never merged into one number.
Numbers: eval_v3, prompt v4, models claude-haiku-5.5 + deepseek-v4.1-flash + gemini-3.7-flash,
rule "accuse only at 3 of 3".

- Honest numbers: every figure shows its n and a 95% Wilson interval (the error bar).
- **Lead with the hardest source: real news headlines annotated by humans (PTC, SemEval-2020
  Task 11), n = 332.** Accuracy 72.3% (240 of 332, 67.2% to 76.8%). Honest headlines wrongly
  accused: 53 of 250 = 21.2% (16.6% to 26.7%). Say: "This is the set we did not pick or label
  ourselves, and it is the one where we look worst."
- **Why the rule is 3 of 3:** a false accusation costs more than a miss. On PTC the looser 2-of-3
  rule would accuse 85 of 250 = 34% of honest headlines while catching 87% of manipulative ones;
  3 of 3 accuses 21% and catches 66% (54 of 82). Both rows are in the trade-off table, computed
  from the same votes. The rule and the model swap were chosen on dev data, then the frozen test
  was run once.
- **SemEval-2021 (meme texts, human), n = 135:** wrongly accused 1 of 41 = 2.4% (0.4% to 12.6%);
  accuracy 59.3% (80 of 135). **Headlines labelled by one LLM (not human), n = 70:** wrongly
  accused 0 of 37 (0% to 9.4%); accuracy 72.9% (51 of 70).
- **Calibration:** accuracy by agreement level. PTC: all three agree 72.4% (178 of 246). SemEval:
  all three agree 79.7% (55 of 69), disputed 24.4% (11 of 45). Headlines: 88.9% (48 of 54) at
  3 of 3, 14.3% (1 of 7) disputed.
- **Why PTC looks harder:** PTC annotators labelled titles inside whole articles and left many
  plainly loaded titles unmarked ("The Cunning CIA", "Bombshell ...", "A Slap On The Wrist"), so
  part of that 21% is label noise. We did not relabel anything; the caveat is on the page.
- **Human vs model section:** counts only (disputes per answer, status, which reference techniques are
  disputed most, which technique players proposed instead), always with n. If nobody has disputed
  yet, it says so. Disputes are opinions, not labels.
- Known limitations are on the page: domain shift (memes, 2017-2019 articles), possible training-data
  leakage, techniques outside our six are excluded rather than counted as "no technique", small n per
  technique, and a strict rule that misses about a third of manipulative headlines.

### 5. Analyze on a live article (about 40 s)

- Open "Analyze a text", paste the headline and first paragraph of a current news article, or paste the
  article URL (https only). Pick an article beforehand and run it once, so the answer is cached.
- Show: the technique, highlighted evidence words (exact substrings of the text), the explanation and
  the confidence. If the verdict is "Disputed", leave it: that is the honest outcome.
- Mention the protections: per-IP rate limit (20 requests per minute), a global daily cap on
  analyses (default 500 per local day; each analysis costs three model requests), and a URL fetch
  guard against requests to private networks.

### 6. The daily challenge and the nightly job (about 30 s)

- Say: "There is one challenge per day for everyone. At 00:00 Europe/Helsinki a job collects headlines
  from 35 English-language outlets with different declared leanings, groups them by event, labels them
  with the ensemble and sets the daily round. It prefers an event where at least one headline carries
  a technique, and when none does, it still sets the most varied round rather than leaving the day
  empty. It never relabels anything to force variety."
- After the last clipping the board shows the closed edition and the time of the next one.

## Q&A cheat sheet (honest answers)

Numbers: eval_v3, prompt v4, claude-haiku-5.5 + deepseek-v4.1-flash + gemini-3.7-flash, accuse only at
3 of 3. "PTC" is the 332 human-labelled news headlines.

**Why three models?** One model's confidence says little about whether it is right. Three vendors voting
gives an agreement signal, and on PTC the ensemble beats every single model on both counts: accuracy
72.3% against 67.1% (claude-haiku), 65.7% (gemini) and 61.6% (deepseek); honest headlines accused 21%
(53 of 250) against 31%, 36% and 42% for the models alone. The per-model table is in Methodology.

**Does the ensemble always beat the best single model?** No, and we show it. On the LLM-labelled
headlines deepseek alone has 85.7% accuracy against the ensemble's 72.9%: the strict rule drops every
2-of-3 call there. On SemEval deepseek alone is 60.9% against 59.3%. The ensemble's value is the
agreement signal and far fewer false accusations (SemEval: 1 of 41 against 11 to 19 of 41).

**Why accuse only at 3 of 3?** A false accusation costs more than a miss. On PTC the 2-of-3 rule accuses
34% of honest headlines and catches 87% of manipulative ones; 3 of 3 accuses 21% and catches 66%. Both
rows are on the page. The rule and the model swap (gemini-3.7-flash for gpt-5.4-nano) were chosen on
dev data (seven candidate models, every trio scored offline), then the frozen test was run once. On dev
the swap gain was within noise; we say so.

**Why cheap models?** Cost: a public Analyze button costs three model requests per call. On dev, the
expensive claude-sonnet-5.5 alone flagged more clean texts than claude-haiku, so price did not buy
precision here. We do not claim the cheap choice is optimal.

**How do the data change the detector?** The detector is still prompted LLMs, not a trained model. What
the labelled data do: (1) the frozen test sets measure it; (2) the development set (527 meme texts plus
81 real headlines labelled by one LLM annotator, not humans) supplies the examples in the prompt. For
each text the prompt gets 14 fixed anchors plus the 4 most similar labelled dev examples (TF-IDF
similarity, news headlines preferred). On dev, leave-one-out (each dev text scored without itself),
retrieval against anchors only: accuracy 81.5% to 85.2%, macro-F1 0.58 to 0.53, same false-positive
rate. On the frozen eval_v2 test sets (before the 3-of-3 rule), measured once: headlines accuracy 78.6% to 78.6% (unchanged) and macro-F1
0.30 to 0.39; SemEval accuracy 60.7% to 61.5%, macro-F1 0.33 to 0.37, but false positives 7 of 41 to 10
of 41. Honest summary: no clear gain; all intervals overlap. We keep retrieval because it is the
data-driven route and macro-F1 rose on both sources, and we say plainly that it is not proven better.
Anchors-only is one setting away (`FEWSHOT_MODE=static`).

**Did a first result look better than the final one?** Yes, and we threw it away. A first run showed
headlines accuracy 82.9%. A review then found 11 development headlines about the same stories as test
headlines (same event, different outlet), so retrieval could have shown the model near-copies. We removed
them from dev, replaced one prompt anchor, and measured again: the gain vanished. This is exactly why the
frozen test set matters, and it is the honest version of the numbers above.

**Is there leakage?** Two separate risks. PTC itself was never used for tuning: no PTC text is in dev or
in the prompt (overlap check: 0). (1) Our own pipeline: the test sets were frozen before any
prompt tuning, few-shot examples come only from the development set, and dev and test are checked for
duplicates on normalized text, and also for same-story near-duplicates by content-word overlap (that
second check found 11 in our first dev set; they were removed, see the question about the first result
above). Confirmed player disputes go to dev only. (2) The models themselves: the
public datasets may be in their training data, which can inflate scores. We cannot rule that out and
the page says so. PTC is public since 2019, so this risk is real for it; the RSS headlines fetched
2026-11-14 are the least exposed.

**Domain shift?** PTC is the right domain with human labels, but its articles are from 2017-2019 and
lean partisan; its annotators labelled titles inside whole articles and left some plainly loaded titles
unmarked ("The Cunning CIA", "Bombshell ..."), so part of our 21% is label noise. We did not relabel.
SemEval-2021 is meme text; the newest headlines are LLM-labelled. Each source is shown separately.

**Is n enough?** PTC has 332 headlines (250 clean), so the false-accusation rate has a usable
interval (17% to 27%). Per technique it is small: on PTC appeal to fear 3, exaggeration 3, doubt 7,
appeal to authority 1, changing the subject 1. Loaded language is the one technique we detect well
(40 of 67 on PTC); the rare ones are mostly missed under the strict rule (doubt 1 of 7).

**What is not measured?** A human ceiling (no second annotator, so no Cohen's kappa); techniques outside
our six (excluded, not counted); long articles (we test headlines and short texts); precision of
span highlighting beyond token-F1 (0.37 on PTC, 0.61 when the technique is right); anything about bots
or coordinated accounts.

**Why is the daily round often mild?** Most real headlines are plain, and the strict 3-of-3 rule makes
the detector accuse fewer of them. The daily job still requires at least one headline with a technique
when the day's pool has one. We did not loosen the rule to get variety: that trades variety for false
accusations.

**What would you do with two human annotators?** Label a gold set of 150 to 200 real headlines from our
own pipeline, independently, before seeing any model output, with about 35 to 40% "no technique". Compute
Cohen's kappa as the human ceiling, let a third person settle disagreements, replace the LLM-labelled
source, freeze it as a new test version, and only then tune prompts, on the dev set.

**Can players poison the labels?** No. A dispute is an opinion. One vote changes nothing, review needs
several different players or a team decision, a reviewer assigns the label, and the result goes only to
the dev set. The frozen test set is never touched and never used for few-shot examples.

**What stops someone from running up the bill?** A per-IP rate limit, a global daily cap on analyses
(returns a friendly HTTP 429 once reached), a verdict cache so a repeated text does not call the models
again (cached texts still count toward the daily cap), and a size limit on texts and request bodies.

## Pre-demo checklist

- [ ] Public URL opens from a clean device; `/health` answers 200.
- [ ] The deployed version is the one you expect (it includes the dispute stats and the daily cap).
  Deployment is a separate step that needs an explicit yes.
- [ ] Today's daily round exists: the board shows an event and three clippings, not "no rounds". If it
  is missing, run the daily job again inside the container (`python -m app.news.daily --rebuild-today`).
- [ ] Play today's round once on your own browser profile, or use a second profile for the demo: the
  daily challenge can be played once per browser, and the closed edition is shown afterwards.
- [ ] Key balance: check the Hostinger AI Router balance. Cached verdicts replay for free, but a new
  Analyze text costs three model requests.
- [ ] Analyze: run your chosen texts once beforehand so the live answer is instant. Checked and cached on
  2026-11-15 (paste exactly, typographic quotes included): "Kentucky couple’s dream home turns into
  snake-infested house of horrors" (loaded language, 3 of 3); "Confirmed: Authorities LIED About Las Vegas
  Shooter’s Hotel Check-In Date – What Else Are They Hiding?" (doubt, 3 of 3); "US flights chaos sparks UK
  travel misery amid cancellations after FAA ground stop" (2 of 3, not accused); "US bloggers banned from
  entering UK" (no technique, 3 of 3).
- [ ] /metrics shows the "live" run, not a stub banner, and the numbers match this file.
- [ ] Human vs model section: either empty (say so) or show real counts. Do not create fake disputes.
- [ ] Radio is off (the masthead toggle shows "off"; the choice is remembered per browser).
- [ ] Fallback if the network or server fails: open each page with `?mock=1` (for example `/metrics/?mock=1`).
  The mock numbers are invented and a banner says so: tell the judges plainly that this is a
  fallback and read the real numbers from this file.
- [ ] Phone check: the pages work at phone width without sideways scrolling.
