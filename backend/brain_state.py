from enum import Enum

class BrainState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


current_state = BrainState.IDLE

def get_state():
    return current_state

def set_state(new_state: BrainState):
    global current_state
    current_state = new_state