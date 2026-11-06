# Third-Party Data Notices

## ECDICT

The bundled vocabulary data in `vocab/CET4.json`, `vocab/CET6.json`,
`vocab/Kaoyan.json`, `vocab/TOEFL.json`, and the importance ranks in
`importance.json` were generated from **ECDICT**:

- Source: https://github.com/skywind3000/ECDICT
- Source commit: `39cfb9672c0906b63569e0453c2e6abfb9ab182a`
- License: MIT License, Copyright (c) 2025 Linwei
- Full license text: [`LICENSES/ECDICT-MIT.txt`](LICENSES/ECDICT-MIT.txt)

### Changes made for this project

- Selected entries by ECDICT exam tags: `cet4`, `cet6`, `ky` (考研), and
  `toefl`.
- Kept the `word`, `phonetic`, and `translation` fields for each vocabulary
  entry; converted literal `\n` sequences in translations to line breaks,
  added surrounding slashes to phonetics without them, and set `phrases` to
  an empty list.
- Built `importance.json` from ECDICT `bnc` and `frq` ranks. Only entries
  carrying an exam tag or a Collins/Oxford core marking contribute a rank;
  the rank is the smaller positive value of `bnc` and `frq`. The original
  ECDICT CSV is not redistributed.

The generated files are distributed under the MIT license terms above.
