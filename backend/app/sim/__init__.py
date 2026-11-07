"""M7: simulated residents. The shared voice spec talks over text with a resident model that plays
a household's hidden truth; each call is stored for replay through the engine (M9).

    persona.resident_prompt(household, truth)   # the resident's system prompt
    conversation.simulate_call(llm, h, truth)   # -> SimRun (a CallResult plus tokens and tool calls)
    store.OutcomeStore / store.SimKey           # data/cache/sim_outcomes.db
    batch.run_batch(...)                        # concurrency cap, resume, progress
"""
