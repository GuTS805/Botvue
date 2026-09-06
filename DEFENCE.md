# Questions this project should expect

Answers to the objections a sceptical reader should raise. Each one is meant to be
answerable in about twenty seconds, from evidence rather than assertion.

---

**"This is just anti-bot protection. Sites are allowed to block crawlers."**

Agreed, and that is why blocking is not what this measures. Gannett returns `402` on 24
properties and Advance returns `403` on 11. Those are refusals, this reports them, and it
tells the calling agent to carry on — a 4xx already tells the agent it got nothing.

The finding is the ones that refuse while reporting success. The question is not *may they
block*. It is *why does the status code say otherwise*.

---

**"They probably can't return a proper status code through that vendor."**

The same vendor returns the correct one 126 times. The byte-identical TollBit body
(`ad3a87823990…`) appears 182 times across 64 properties: 126 carry `402`, 56 carry `200`.
Same bytes, same integration, different setting.

---

**"Couldn't that be a transient failure rather than a configuration?"**

Transient failures do not hold still. Re-fetch `houstonchronicle.com` as GPTBot now and you
get `32ed63159c77…`, the same body recorded on 6 September. Fetch it as Googlebot and the
hash is different every time, because the homepage is alive.

The empty page is frozen; the real page moves. Only one of those is a configuration.

---

**"Maybe Hearst just blocks bots as company policy."**

Hearst's 22 newspapers serve GPTBot an empty page. Hearst's 19 magazines serve it the
article. Same company, opposite behaviour, one division apart.

---

**"Your classifier is only 58% accurate."**

Correct, and that is our own measurement of our own weakest signal — see
[`scanner/eval.py`](scanner/eval.py). It is why that signal does not drive any decision. A
`substituted` verdict requires an explicit marker or a response header the browser never
receives; unmatched text alone gets `crawler-only-text`, which is reported and never acted
on.

Soft-block detection does not use the classifier at all. It is a ratio of word counts.

---

**"How did you pick the threshold for 'empty'?"**

There is not one to pick. Across 1,483 crawler responses the distribution is bimodal: 6 fall
below 5% of the human page, 1,477 sit above 60%, and nothing lands in between. A crawler
either gets the page or gets nothing.

That gap is only visible because the corpus was fetched; it is not a parameter that was
tuned.

---

**"Why a blockchain? A Postgres table would do."**

A table would prove that we wrote something down. The topic has no submit key, so anyone can
append to it — including a publisher who disputes a finding. Verification reads Hedera's
public mirror node and re-fetches pages from the publishers, so a reader trusts neither our
database nor our account of what we saw.

These responses carry `cache-control: no-store`. Nothing else survives.

---

**"What's the scale? Is this a handful of sites?"**

539 pages scanned, 129 properties with a finding, 460 domains in the wider corpus. 32
properties soft-blocking, 84 refusing honestly, across six publisher groups.

---

**"Isn't the corpus cherry-picked?"**

The chains were chosen deliberately, and that is the method rather than a flaw: one CDN
configuration is deployed across a whole network, so testing a chain tests a configuration.
Two of the six groups came back completely clean, and one of those is the other half of
Hearst.

The wider 460-domain corpus was drawn from llms.txt directories and Tranco, not hand-picked.

---

**"Could your tool be wrong about a specific site?"**

Yes, and one is on the page on purpose. `overstock.com` serves a stub to everyone including
Googlebot, so it is reported as not AI-specific and excluded from the count — existing
checkers would catch that one.

Every record is a hash of a body that can be fetched again. If one is wrong, it can be shown
to be wrong.

---

**"What does the agent actually do with this?"**

Refuses to read the page. The gateway returns `block`, and the demo agent stops:

> Without the check, this agent would have treated the response it was given as the article,
> and reported on something it never read.

---

**"Does this work outside news?"**

Unknown, and not claimed. The corpus is publisher-heavy because that is where the behaviour
was found. The method is not news-specific, but the measurement is.
