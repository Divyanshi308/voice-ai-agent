from voice_pipeline import VoicePipeline

pipeline = VoicePipeline()

session = pipeline.get_or_create_session('test', 0)

# Test multiple different questions
tests = [
    ('hello', 'First message'),
    ('my name is Priya', 'Name'),
    ('I have a billing problem', 'Billing issue'),
    ('thanks', 'Thank you'),
    ('bye', 'Goodbye'),
]

for text, desc in tests:
    r = pipeline.flow_engine.process_turn(session, text)
    print(f'{desc}: "{text}" -> AI: {r[:80]}...')

print()
print('=== Session Info ===')
summary = session.get_summary()
print(f'Phase: {summary["phase"]}')
print(f'Language: {summary["language"]}')
print(f'Collected: {summary["collected_info"]}')
print(f'Turns: {summary["total_turns"]}')
print(f'Interruptions: {summary["interruptions"]}')

pipeline.end_session('test')