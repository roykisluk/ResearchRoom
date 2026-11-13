# Findings so far
updated: 2026-11-03

## Picture
A made-up study, to show how the overview reads this file: a `## F<n>.` heading, a few `key: value` lines, then prose.
Northvale, an invented region, raised its minimum wage from 10.00 to 12.50 in January 2019. Every number is made up.

## F1. Employment did not fall after the raise
importance: high
changed: 2026-11-03
added: 2026-10-06
type: event study
tags: Main effect
purpose: Tests whether jobs were lost once the minimum rose.
does: Compares Northvale's counties with their neighbours, year by year.
shows: Employment tracks the neighbours after the raise, while wages pull ahead.
exhibit: figures/02_event_study_v2_employment.pdf
also: figures/02_event_study_v2_wages.pdf
Places where the new minimum bit harder lost no more jobs either (the binned scatter).

## F2. Most of the wage gain came from raises within firms
importance: med
changed: 2026-11-03
added: 2026-11-03
type: bars
tags: Mechanism
purpose: Splits the wage gain into raises within firms and moves between them.
does: Decomposes the change in the mean wage.
shows: Raises within firms are the largest part; moves to better-paying firms add a smaller part.
exhibit: figures/07_decomposition_v1.png

## Next
- Replace this file with your own findings, or delete it: the room works without it.
