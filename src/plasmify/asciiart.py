"""Starting points for drawn terminal logos. Users can pick one and edit it freely.

Every sample passes ``schema.ascii_art``: no tabs and no ``$`` (fastfetch reads
``$1``..``$9`` as colour codes).
"""

from __future__ import annotations

SAMPLES: dict[str, str] = {
    "Owl 0-0": r"""
   ,___,
   [0-0]
   /)  )
  ==""==
""",
    "Cat": r"""
   /\_/\
  ( o.o )
   > ^ <
  /     \
 (  | |  )
""",
    "Bunny": r"""
  (\_/)
  (o.o)
  (> <)
 o(_)(_)
""",
    "Bear": r"""
  ()_()
  (o o)
 ==\o/==
 /     \
(  ) (  )
""",
    "Ghost": r"""
   .~~~~.
  / o  o \
 |   __   |
 |        |
  VVVVVVVV
""",
    "Penguin": r"""
    .~~.
   | o_o|
   |:_/ |
  //   \ \
 (|     | )
/'\_   _/`\
\___)=(___/
""",
    "Peak": r"""
        /\
       /  \
      /    \
     /  /\  \
    /  (  )  \
   /  /    \  \
  /__/      \__\
""",
    "Wave": r"""
   .  ~  .  ~  .
 ~   ~   ~   ~   ~
~ ~ ~ ~ ~ ~ ~ ~ ~ ~
 ~~~ ~~~ ~~~ ~~~ ~~
""",
    "Heart": r"""
  .:::.   .:::.
 :::::::.:::::::
 :::::::::::::::
 ':::::::::::::'
   ':::::::::'
     ':::::'
       ':'
""",
}


def sample(name: str) -> str:
    """A sample without the blank first and last line used to keep the source readable."""
    return SAMPLES[name].strip("\n")
