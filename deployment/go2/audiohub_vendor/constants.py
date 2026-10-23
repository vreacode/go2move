DATA_CHANNEL_TYPE = {
    "VALIDATION": "validation",
    "SUBSCRIBE": "subscribe",
    "UNSUBSCRIBE": "unsubscribe",
    "MSG": "msg",
    "REQUEST": "req",
    "RESPONSE": "res",
    "VID": "vid",
    "AUD": "aud",
    "ERR": "err",
    "HEARTBEAT": "heartbeat",
    "RTC_INNER_REQ": "rtc_inner_req",
    "RTC_REPORT": "rtc_report",
    "ADD_ERROR": "add_error",
    "RM_ERROR": "rm_error",
    "ERRORS": "errors",
}

AUDIO_API = {
    # Audio Player Commands
    "GET_AUDIO_LIST": 1001,
    "SELECT_START_PLAY": 1002,
    "PAUSE": 1003,
    "UNSUSPEND": 1004,
    "SELECT_PREV_START_PLAY": 1005,
    "SELECT_NEXT_START_PLAY": 1006,
    "SET_PLAY_MODE": 1007,
    "SELECT_RENAME": 1008,
    "SELECT_DELETE": 1009,
    "GET_PLAY_MODE": 1010,
    
    # Audio Upload
    "UPLOAD_AUDIO_FILE": 2001,
    
    # Internal Corpus
    "PLAY_START_OBSTACLE_AVOIDANCE": 3001,
    "PLAY_EXIT_OBSTACLE_AVOIDANCE": 3002,
    "PLAY_START_COMPANION_MODE": 3003,
    "PLAY_EXIT_COMPANION_MODE": 3004,
    
    # Megaphone
    "ENTER_MEGAPHONE": 4001,
    "EXIT_MEGAPHONE": 4002,
    "UPLOAD_MEGAPHONE": 4003,
    
    # Internal Long Corpus
    "INTERNAL_LONG_CORPUS_SELECT_TO_PLAY": 5001,
    "INTERNAL_LONG_CORPUS_PLAYBACK_COMPLETED": 5002,
    "INTERNAL_LONG_CORPUS_STOP_PLAYING": 5003
}
