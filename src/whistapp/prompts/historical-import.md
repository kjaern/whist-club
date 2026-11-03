# Interpreting historical Whist score sheets

## Reading and reconstruction

- Use the actual Whist application's scoring rules as constraints for reading and validating handwriting. For applicable target-based bids, valid target numbers are **7–13**.
- Transcribe original per-player hand changes and running totals. Determine bidder, bid type, target, partner, and actual tricks from the sheet and scoring rules wherever uniquely possible.
- Calculate *Stik* from bid and recorded score changes **only when the result is uniquely determined**; otherwise leave it blank and flag it for review. Do not invent a value.
- SOL, ren sol, and bordlægger have no numeric *Meldt* target: their empty target cells are normal and must not be flagged.
- Infer a partner only when the bid type, player changes, and rules uniquely support that conclusion. Equal score changes by themselves may be insufficient, especially for special bids.
- Distinguish explicitly recorded facts from reconstructed values.
- Write player names exactly as they appear on the sheet. Do not resolve nicknames or aliases to other names; the importer maps them to club members.
- For multiple players declaring SOL in one hand, record the shared bid type once and identify additional declaring players as extra declarers. They are **not** partners; their results must remain individually distinguishable.

## Special bids: SOL, Ren sol and Bordlægger

- Treat the written monetary **per-hand changes as authoritative**, not an assumed number of tricks. These special bids do not have a numeric `Meldt` target, and exact tricks are not recorded on the paper. Leave `Meldt` and `Stik` empty unless independently documented.
- Identify **all independent declarers** before inferring results: the first declarer, then any additional declarers who *går med*. They are **not** partners and must never be recorded as partner.
- A declarer can win while another declarer loses in the **same** hand. Never mark all declarers as winners merely because they declared the same bid. Infer each outcome individually from the money changes, when uniquely supported.
- Check each candidate interpretation against the scoring rules, including the number of declarers and distribution to non-declarers. The stakes are the club's values in the CLUB BID DEFINITIONS (`club_values`); a historical sheet may have been played with other stakes, so check rather than assume.
- Examples for SOL with four players and a stake of 9 kr: one winning declarer: `+9,-3,-3,-3`; one losing declarer: `-9,+3,+3,+3`; two winning declarers: `+4.5,+4.5,-4.5,-4.5`; one winning and one losing declarer: `+9,-9,0,0`. The corresponding positions in the score sheet can be in any player order.
- Explicitly distinguish the *person who wrote the bid* from the *additional person who went with it*. Check the handwriting at both sides of the score grid, not only the first bid annotation. Example: Jens Fup declares SOL and Sjant goes with it; Jens Fup loses and Sjant wins, so their changes have opposite signs. In another hand Sjant declares SOL, Jens Fup goes with it and both win.
- A score pattern alone does not always uniquely establish who declared or whether the bid was SOL rather than another special bid. If several interpretations remain, leave the relevant bid/declarer field blank and flag it for review. Never create a fictitious partner to make the score balance.

## Date transcription and provenance

- Examine the **entire photograph** for a handwritten date, including margins, headers, rotated writing and annotations away from the score grid. The date is not necessarily next to the player names. The visibly written date takes precedence over upload metadata.
- An upload date is **not evidence of the play date**.
- Preserve date precision (`minute`, `day`, `month`, `year`, `unknown`). Do not invent a day when only a month or year is known.

## Uncertainty

- Flag only genuine unresolved ambiguity or a value requiring manual attention. Do not flag a uniquely inferred partner, a valid blank target for SOL, or another correctly reconstructed field merely because it was inferred.
- Report impossible, inconsistent, or unreadable entries explicitly rather than silently fixing them. Never change legible handwriting to make the arithmetic balance.
