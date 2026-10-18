"""Cyph's colours, taken from the fursona: teal hair, mint fur, neon-green
circuit lines on an olive jacket, coral inner ear, cream eyes, warm amber
poster-wall background."""

BG        = "#10150e"   # deep olive night
PANEL     = "#182014"   # jacket shadow
PANEL_HI  = "#223019"
JACKET    = "#4a5035"   # olive jacket
NEON      = "#5dff7a"   # circuit-line glow
MINT      = "#8be37a"   # fur
TEAL      = "#2f9d8f"   # hair
DEEP_TEAL = "#1c6b6b"
SKY       = "#5b9fb8"   # poster-mountain blue
CREAM     = "#f3ecb2"   # eyes
CORAL     = "#f08a73"   # inner ear
AMBER     = "#d9984a"   # poster glow
RED       = "#e0584c"
DIM       = "#6b7a5e"

MOOD_COLOR = {
    "happy":   MINT,
    "excited": NEON,
    "curious": CREAM,
    "sniff":   TEAL,
    "sleepy":  SKY,
    "sad":     "#7aa5c4",
    "startle": CORAL,
    "purr":    AMBER,
    "zoomies": "#ff9ed2",
    "proud":    "#e8c95a",
    "shy":      "#e8a0b4",
    "grumpy":   RED,
    "love":     "#ff7aa8",
    "laugh":    "#ffd166",
    "confused": "#b79cff",
    "yawn":     "#8fb8c9",
    "stretch":  "#9be3b0",
    "nervous":  "#c9c46a",
    "playbow":  "#7cf0d0",
    "dance":    "#c77dff",
    "think":    "#7fd1e8",
    "boop":     "#ffb86b",
    "awoo":     "#a0b8ff",
    "wave":     "#6fe3a0",
    "smug":     "#d8a8ff",
    "sneeze":   "#e8e8a0",
    "rest":    DIM,
}

JOINT_COLOR = [TEAL, SKY, MINT, NEON, CREAM, CORAL]

LEVEL_COLOR = {
    "mood": MINT,
    "info": CREAM,
    "warn": AMBER,
    "error": RED,
    "you": SKY,
    "cyph": NEON,
    "tool": TEAL,
    "debug": DIM,
}

# eyes, mouth for each state (ASCII-friendly so any terminal font works)
FACES = {
    "rest":    ("•", "•", "‿"),
    "happy":   ("^", "^", "ω"),
    "excited": (">", "<", "D"),
    "curious": ("o", "O", "~"),
    "sniff":   ("•", "•", "·"),
    "sleepy":  ("-", "-", "z"),
    "sad":     ("T", "T", "︵"),
    "startle": ("O", "O", "o"),
    "purr":    ("=", "=", "ω"),
    "zoomies": ("@", "@", "▽"),
    "proud":    ("^", "^", "‿"),
    "shy":      ("˘", "˘", "·"),
    "grumpy":   ("\\", "/", "︿"),
    "love":     ("♥", "♥", "ω"),
    "laugh":    ("^", "^", "D"),
    "confused": ("o", "•", "~"),
    "yawn":     ("-", "-", "O"),
    "stretch":  ("-", "-", "▽"),
    "nervous":  ("°", "°", "﹏"),
    "playbow":  ("^", "^", "▽"),
    "dance":    ("♪", "♪", "▽"),
    "think":    ("•", "¬", "·"),
    "boop":     ("•", "•", "o"),
    "awoo":     ("^", "^", "O"),
    "wave":     ("^", "^", "ω"),
    "smug":     ("¬", "¬", "ω"),
    "sneeze":   (">", "<", "o"),
}
