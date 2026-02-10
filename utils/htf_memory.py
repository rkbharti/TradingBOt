import json
import os

class HTFMemory:
    def __init__(self, file="htf_memory.json"):
        self.file = file
        self.state = {}
        self.load()

    def load(self):
        if os.path.exists(self.file):
            try:
                with open(self.file, "r") as f:
                    self.state = json.load(f)
            except:
                self.state = {}

    def save(self):
        with open(self.file, "w") as f:
            json.dump(self.state, f, indent=4)

    def update(self, key, value):
        self.state[key] = value
        self.save()

    def get(self, key, default=None):
        return self.state.get(key, default)
