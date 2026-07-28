"""SYNTHETIC RL execution sandbox - NOT EVIDENCE. Produces no promotable result.

See the module docstring notes in the generated report. Every environment number was
CHOSEN, not measured. The original version paid a maker REBATE of +1.5 bps; Binance USD-M
charges a maker FEE of 2.0 bps (event_conditional_v1/frozen_protocol.json). Flipping only
that sign turns the agent from +0.57 bps to -2.88 bps - the "win" was an invented rebate.

    python backend/ppo_execution_test.py
"""
import numpy as np
import os
import random

# Sourced from event_conditional_v1/frozen_protocol.json, not invented.
TAKER_FEE_BPS = 5.0
MAKER_FEE_BPS = 2.0
IMPACT_BPS = 1.0

class OrderBookExecutionEnv:
    """
    Simulates a Level-2 Order Book Execution Environment.
    Goal: Buy 1 unit of BTC at the best possible price before time runs out.
    """
    def __init__(self, max_steps=10):
        self.max_steps = max_steps
        self.reset()
        
    def reset(self):
        self.time_left = self.max_steps
        # 0: Narrow, 1: Wide
        self.spread = random.choice([0, 1])
        # 0: Not in queue, 1: Back of book, 2: Top of book
        self.queue_pos = 0 
        self.filled = False
        return self._get_state()
        
    def _get_state(self):
        # Discretize time: 0 (Low), 1 (Medium), 2 (High)
        t_state = 0 if self.time_left <= 2 else (1 if self.time_left <= 5 else 2)
        return (t_state, self.spread, self.queue_pos)
        
    def step(self, action):
        """
        Actions:
        0: WAIT (If in queue, stay in queue. If not, do nothing)
        1: MAKER (Place limit order at best bid. Resets queue pos to Back)
        2: TAKER (Cross spread. Immediate fill, high cost)
        """
        reward = 0
        done = False
        
        self.time_left -= 1
        
        # Spread dynamics
        if random.random() < 0.2:
            self.spread = 1 - self.spread
            
        if action == 2: # TAKER
            self.filled = True
            done = True
            # Taker fee (e.g. 5 bps) + Slippage (Spread cost)
            slippage = 1.0 if self.spread == 0 else 3.0
            reward = -TAKER_FEE_BPS - slippage
            
        elif action == 1: # MAKER
            self.queue_pos = 1 # Back of book
            reward = -0.5 # Small cost for canceling/replacing
            
        elif action == 0: # WAIT
            if self.queue_pos > 0:
                # Progress in queue
                if self.queue_pos == 1: # Move to top
                    if random.random() < 0.5:
                        self.queue_pos = 2
                elif self.queue_pos == 2: # Chance to fill
                    fill_chance = 0.8 if self.spread == 0 else 0.3
                    if random.random() < fill_chance:
                        self.filled = True
                        done = True
                        # Maker FEE, not a rebate: Binance USD-M charges 2.0 bps
                        # at the tier frozen_protocol.json assumes.
                        reward = -MAKER_FEE_BPS 
            
        # Forced Liquidation if time runs out
        if self.time_left <= 0 and not self.filled:
            done = True
            # Forced market taker + massive penalty for failing to execute properly
            slippage = 1.0 if self.spread == 0 else 3.0
            reward = -15.0 - slippage
            
        return self._get_state(), reward, done
        
class RL_Agent:
    def __init__(self, alpha=0.1, gamma=0.9, epsilon=0.1):
        # Q-Table: State -> Action values
        # States: Time (3) x Spread (2) x Queue (3) = 18 states
        # Actions: 3
        self.q_table = np.zeros((3, 2, 3, 3))
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon
        
    def choose_action(self, state, exploit=False):
        t, s, q = state
        if not exploit and random.random() < self.epsilon:
            return random.randint(0, 2)
        return np.argmax(self.q_table[t, s, q])
        
    def learn(self, state, action, reward, next_state, done):
        t, s, q = state
        nt, ns, nq = next_state
        
        best_next = np.max(self.q_table[nt, ns, nq]) if not done else 0
        target = reward + self.gamma * best_next
        self.q_table[t, s, q, action] += self.alpha * (target - self.q_table[t, s, q, action])

def train_and_evaluate():
    env = OrderBookExecutionEnv()
    agent = RL_Agent()
    
    # Train
    episodes = 20000
    for _ in range(episodes):
        state = env.reset()
        done = False
        while not done:
            action = agent.choose_action(state)
            next_state, reward, done = env.step(action)
            agent.learn(state, action, reward, next_state, done)
            state = next_state
            
    # Evaluate Agent vs Naive Taker
    eval_episodes = 1000
    rl_rewards = []
    naive_rewards = []
    
    for _ in range(eval_episodes):
        # RL Agent
        state = env.reset()
        done = False
        ep_reward = 0
        while not done:
            action = agent.choose_action(state, exploit=True)
            state, r, done = env.step(action)
            ep_reward += r
        rl_rewards.append(ep_reward)
        
        # Naive Taker - FULL episode, same as the agent. The original compared a single
        # step against a full episode, which flattered the agent for free.
        state = env.reset()
        done = False
        ep = 0.0
        while not done:
            state, r, done = env.step(2)
            ep += r
        naive_rewards.append(ep)
        
    return agent.q_table, np.mean(rl_rewards), np.mean(naive_rewards)

def write_results_to_docs(q_table, rl_mean, naive_mean):
    doc_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docs",
                            "ppo_execution_results.md")
    time_labels = ["Low", "Medium", "High"]
    spread_labels = ["Narrow", "Wide"]
    queue_labels = ["None", "Back", "Top"]
    action_labels = ["WAIT", "MAKER", "TAKER"]

    L = []
    L.append("# RL Execution Sandbox - SYNTHETIC, NOT EVIDENCE")
    L.append("")
    L.append("> **Not promotable. Must not be cited as an edge.** Every environment number")
    L.append("> - fees, fill probabilities, queue advancement, penalties - was CHOSEN, not")
    L.append("> measured. The agent learns the environment's author, not the market. The")
    L.append("> multi-venue archive has 0 rows, so no fill model has been validated against")
    L.append("> reality.")
    L.append("")
    L.append("## What the first version got wrong")
    L.append("")
    L.append("It paid a maker **rebate** of +1.5 bps. Binance USD-M charges a maker **fee** of")
    L.append("2.0 bps at the tier `event_conditional_v1/frozen_protocol.json` assumes.")
    L.append("Flipping only that one sign, everything else identical:")
    L.append("")
    L.append("```text")
    L.append("maker rebate +1.5 (as written)   agent mean  +0.57 bps")
    L.append("maker fee    -2.0 (real venue)   agent mean  -2.88 bps")
    L.append("```")
    L.append("")
    L.append("The reported 88% win was an artifact of an invented rebate. The benchmark was")
    L.append("also unfair - the naive comparator ran ONE step against the agent's full")
    L.append("episode. Both are corrected below.")
    L.append("")
    L.append("## Corrected run: frozen-protocol fees, fair benchmark")
    L.append("")
    L.append("| policy | mean episode cost |")
    L.append("|---|---:|")
    L.append(f"| naive taker (full episode) | `{naive_mean:+.2f} bps` |")
    L.append(f"| trained agent | `{rl_mean:+.2f} bps` |")
    L.append(f"| difference | `{rl_mean - naive_mean:+.2f} bps` |")
    L.append("")
    L.append("**Both policies are net NEGATIVE.** Patience reduces cost relative to always")
    L.append("crossing, but it does not produce profit - there is no rebate to harvest. Any")
    L.append("apparent edge here is a property of this hand-written simulator.")
    L.append("")
    L.append("## What would make this real")
    L.append("")
    L.append("Fill probabilities and queue dynamics measured from the recorded L2 tape; the")
    L.append("venue's actual fee schedule; adverse selection after fill; missed-fill")
    L.append("opportunity cost; and the TRADE_THROUGH / QUEUE_ESTIMATED fill standards already")
    L.append("defined in `event_conditional_v1`. None are available at 0 archive rows.")
    L.append("")
    L.append("## Policy converged on IN THIS SIMULATOR ONLY")
    L.append("")
    L.append("```text")
    L.append("Time    Spread    Queue      -> Action")
    L.append("--------------------------------------")
    for ti in range(3):
        for s in range(2):
            for q in range(3):
                a = action_labels[int(np.argmax(q_table[ti, s, q]))]
                L.append(f"{time_labels[ti]:<8}{spread_labels[s]:<10}{queue_labels[q]:<10} -> {a}")
    L.append("```")
    L.append("")
    L.append("Read as a description of the toy environment's incentives, not as a trading")
    L.append("rule. It says: cross when out of time, wait when already at the front of the")
    L.append("queue. That is what the reward function was written to reward.")
    L.append("")

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(chr(10).join(L))
    print(f"Wrote {doc_path} (marked SYNTHETIC / NOT EVIDENCE)")


if __name__ == "__main__":
    print("Training RL Execution Agent (20,000 episodes)...")
    q_table, rl_mean, naive_mean = train_and_evaluate()
    print("Writing results to docs...")
    write_results_to_docs(q_table, rl_mean, naive_mean)
