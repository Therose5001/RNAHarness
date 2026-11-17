"""Stand-in for Infernal's cmscan: writes a tblout with one 5S_rRNA hit."""
import sys
from pathlib import Path

args = sys.argv
out = Path(args[args.index("--tblout") + 1])
out.write_text(
    "5S_rRNA              RF00001   query                -          cm"
    "        1      120        3      118      +    no    1 0.66"
    "   0.0   92.8   1.8e-21 !   5S ribosomal RNA\n",
    encoding="utf-8",
)
sys.stderr.write("fake cmscan ok\n")
