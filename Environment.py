from Main.Emulator.Emulator import Emulator
from Main.Emulator.Pipes.Address import Address
from Main.SF_Environment.Steps import *
from Main.SF_Environment.Actions import Actions


# Combines the data of multiple time steps
def add_rewards(old_data, new_data):
    for k in old_data.keys():
        if "rewards" in k:
            for player in old_data[k]:
                new_data[k][player] += old_data[k][player]
    return new_data


# Returns the list of memory addresses required to train on Street Fighter
def setup_memory_addresses():
    return {
        "fighting": Address('0x0200EE44', 'u8'),
        "winsP1": Address('0x02011383', 'u8'),
        "winsP2": Address('0x02011385', 'u8'),
        "healthP1": Address('0x02068D0B', 's8'),
        "healthP2": Address('0x020691A3', 's8')
    }


# Converts and index (action) into the relevant movement action Enum, depending on the player
def index_to_move_action(action):
    return {
        0: [Actions.P1_LEFT],
        1: [Actions.P1_LEFT, Actions.P1_UP],
        2: [Actions.P1_UP],
        3: [Actions.P1_UP, Actions.P1_RIGHT],
        4: [Actions.P1_RIGHT],
        5: [Actions.P1_RIGHT, Actions.P1_DOWN],
        6: [Actions.P1_DOWN],
        7: [Actions.P1_DOWN, Actions.P1_LEFT],
        8: []
    }[action]


# Converts and index (action) into the relevant attack action Enum, depending on the player
def index_to_attack_action(action):
    return {
        0: [Actions.P1_JPUNCH],
        1: [Actions.P1_SPUNCH],
        2: [Actions.P1_FPUNCH],
        3: [Actions.P1_JPUNCH, Actions.P1_SPUNCH],
        4: [Actions.P1_SKICK],
        5: [Actions.P1_FKICK],
        6: [Actions.P1_RKICK],
        7: [Actions.P1_SKICK, Actions.P1_FKICK],
        8: [Actions.P1_JPUNCH, Actions.P1_SKICK],
        9: []
    }[action]


# The Street Fighter specific interface for training an agent against the game
class Environment(object):
    
    # difficulty - the difficult to be used in story mode gameplay
    # frameRatio, framesPerStep - see Emulator class
    # render, throttle, debug - see Console class
    def __init__(self, difficulty=3, frame_ratio=3, frames_per_step=3, render=True, throttle=False, debug=False):
        self.difficulty = difficulty
        self.frame_ratio = frame_ratio
        self.frames_per_step = frames_per_step
        self.throttle = throttle
        self.emu = Emulator("sfiii3n", setup_memory_addresses(), frame_ratio=frame_ratio, render=render, throttle=throttle, debug=debug)
        self.started = False
        self.expected_health = {"P1": 0, "P2": 0}
        self.expected_wins = {"P1": 0, "P2": 0}
        self.roundDone = False
        self.gameDone = False
        self.stage = 1

    # Runs a set of action steps over a series of time steps
    # Used for transitioning the emulator through non-learnable gameplay, aka. title screens, character selects
    def run_steps(self, steps):
        for step in steps:
            for i in range(step["wait"]):
                self.emu.step([])
            self.emu.step([action.value for action in step["actions"]])

    # Must be called first after creating this class
    # Sends actions to the game until the learnable gameplay starts
    # Returns the first few frames of gameplay
    def start(self):
        if self.throttle:
            for i in range(int(250/self.frame_ratio)):
                self.emu.step([])
        self.run_steps(set_difficulty(self.frame_ratio, self.difficulty))
        self.run_steps(start_game(self.frame_ratio))
        frames = self.wait_for_fight_start(True)
        self.started = True
        return frames

    # Observes the game and waits for the fight to start
    def wait_for_fight_start(self, is_new_game):
        delay = int(234 / self.frame_ratio) if is_new_game else 0
        data = self.emu.step([])
        while data["fighting"] == 0:
            data = self.emu.step([])
        for i in range(delay):
            data = self.emu.step([])
        self.expected_health = {"P1": data["healthP1"], "P2": data["healthP2"]}
        data = self.gather_frames([])
        return data["frame"]
    
    # To be called when a round finishes
    # Performs the necessary steps to take the agent to the next round of gameplay
    def next_round(self):
        if self.gameDone:
            return self.next_game()
        self.roundDone = False
        return self.wait_for_fight_start(False)

    # To be called when a game finishes
    # Performs the necessary steps to take the agent(s) to the next game and resets the necessary book keeping variables
    def next_game(self):
        wins = self.wait_for_continue()
        if wins["P1"] == 2:
            self.stage += 1
            if self.stage == 11:
                self.stage = 1
        elif wins["P2"] == 2:
            self.stage = 1
        else:
            raise RuntimeError("Environment attempted to reset while player wins on "+str(wins))
        self.run_steps(reset_game(self.frame_ratio, wins))

        self.expected_wins = {"P1": 0, "P2": 0}
        self.roundDone = False
        self.gameDone = False
        return self.wait_for_fight_start(True)

    # Steps the emulator along until the screen goes black at the very end of a game
    def wait_for_continue(self):
        data = self.emu.step([])
        if self.frames_per_step == 1:
            while data["frame"].sum() != 0:
                data = self.emu.step([])
        else:
            while data["frame"][0].sum() != 0:
                data = self.emu.step([])
        return {"P1": data["winsP1"], "P2": data["winsP2"]}

    # Steps the emulator along until the round is definitely over
    def run_till_victor(self, data):
        while self.expected_wins["P1"] == data["winsP1"] and self.expected_wins["P2"] == data["winsP2"]:
            data = add_rewards(data, self.sub_step([]))
        self.expected_wins = {"P1":data["winsP1"], "P2":data["winsP2"]}
        return data

    # Checks whether the round or game has finished
    def check_done(self, data):
        if data["fighting"] == 0:
            data = self.run_till_victor(data)
            self.roundDone = True
            if data["winsP1"] == 2 or data["winsP2"] == 2:
                self.gameDone = True      
        return data

    # Collects the specified amount of frames the agent requires before choosing an action
    def gather_frames(self, actions):
        data = self.sub_step(actions)
        frames = [data["frame"]]
        for i in range(self.frames_per_step - 1):
            data = add_rewards(data, self.sub_step(actions))
            frames.append(data["frame"])
        data["frame"] = frames[0] if self.frames_per_step == 1 else frames
        return data

    # Steps the emulator along by one time step and feeds in any actions that require pressing
    # Takes the data returned from the step and updates book keeping variables
    def sub_step(self, actions):
        data = self.emu.step([action.value for action in actions])

        p1_diff = (self.expected_health["P1"] - data["healthP1"])
        p2_diff = (self.expected_health["P2"] - data["healthP2"])
        self.expected_health = {"P1": data["healthP1"], "P2": data["healthP2"]}

        rewards = {
            "P1": (p2_diff-p1_diff),
            "P2": (p1_diff-p2_diff)
        }

        data["rewards"] = rewards
        return data

    # Steps the emulator along by the requested amount of frames required for the agent to provide actions
    def step(self, move_action, attack_action):
        if self.started:
            if not self.roundDone and not self.gameDone:
                actions = []
                actions += index_to_move_action(move_action)
                actions += index_to_attack_action(attack_action)
                data = self.gather_frames(actions)
                data = self.check_done(data)
                return data["frame"], data["rewards"], self.roundDone, self.gameDone
            else:
                raise EnvironmentError("Attempted to step while characters are not fighting")
        else:
            raise EnvironmentError("Start must be called before stepping")

    # Safely closes emulator
    def close(self):
        self.emu.close()