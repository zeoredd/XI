# abilities/common_abilities/result_codes.py

# Minimal return-code constants for sudo command handling and flows

END = "end"                   # terminate current flow
REFLECT = "reflect"           # mid-session journaling/reflect; keep flow open
SESSION_JOURNAL = "session_journal"  # write session journal without ending
START_PREFIX = "start:"       # start/switch flow prefix, e.g., "start:idea_generator"
NOOP = "noop"                 # recognized but no flow control action

