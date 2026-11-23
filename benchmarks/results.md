### Scenario: excel_model

2030 edited rows; 5019 truly damaged identifier cells; 65 intentional identifier edits

| tool | damaged cells reported | not reported | separates damage from edits | repairs | seconds | note |
| --- | --- | --- | --- | --- | --- | --- |
| csv-safe-restore (default, strict) | 4699 as damage, 320 for review | 0 | yes (0 of 65 edits called damage) | 4699 correct, 0 false, 320 left for review | 1.03 | repair: 1.08 s |
| csv-safe-restore --trust-key | 4879 as damage, 140 for review | 0 | yes (0 of 65 edits called damage) | 4879 correct, 0 false, 140 left for review | 0.90 | repair: 0.85 s |
| daff | 4879 as a changed cell, 140 as an added row | 0 | no (65 of 65 edits listed the same way) | n/a (no repair feature) | 0.62 |  |
| csv-diff | 4986 as a changed cell, 2 as an added row | 31 | no (51 of 65 edits listed the same way) | n/a (no repair feature) | 0.28 |  |
| datacompy | 4982 as a changed cell, 37 as an added row | 0 | no (65 of 65 edits listed the same way) | n/a (no repair feature) | 0.97 | files read with dtype=str |
| UnCorrupt | 0 as a changed cell, 0 as an added row | 5019 | no (0 of 65 edits listed the same way) | n/a (detector only) | 7.87 | single-file scan, no reference; 0 suspicion(s) raised |
| naive revert (baseline, not a tool) | - | - | no | 4989 correct, 65 false, 30 left | - | overwrites every differing identifier cell by key |

### Scenario: libreoffice

2030 edited rows; 3286 truly damaged identifier cells; 65 intentional identifier edits

| tool | damaged cells reported | not reported | separates damage from edits | repairs | seconds | note |
| --- | --- | --- | --- | --- | --- | --- |
| csv-safe-restore (default, strict) | 3120 as damage, 166 for review | 0 | yes (0 of 65 edits called damage) | 3120 correct, 0 false, 166 left for review | 0.78 | repair: 0.80 s |
| csv-safe-restore --trust-key | 3199 as damage, 87 for review | 0 | yes (0 of 65 edits called damage) | 3199 correct, 0 false, 87 left for review | 0.56 | repair: 0.59 s |
| daff | 3199 as a changed cell, 87 as an added row | 0 | no (65 of 65 edits listed the same way) | n/a (no repair feature) | 0.61 |  |
| csv-diff | 3268 as a changed cell, 0 as an added row | 18 | no (51 of 65 edits listed the same way) | n/a (no repair feature) | 0.27 |  |
| datacompy | 3266 as a changed cell, 20 as an added row | 0 | no (65 of 65 edits listed the same way) | n/a (no repair feature) | 0.93 | files read with dtype=str |
| UnCorrupt | 0 as a changed cell, 0 as an added row | 3286 | no (0 of 65 edits listed the same way) | n/a (detector only) | 7.82 | single-file scan, no reference; 0 suspicion(s) raised |
| naive revert (baseline, not a tool) | - | - | no | 3271 correct, 65 false, 15 left | - | overwrites every differing identifier cell by key |
