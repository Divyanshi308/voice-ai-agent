from voice_pipeline import VoicePipeline

pipeline = VoicePipeline()

print("=== Test: Complete Conversation Flow ===")
session = pipeline.get_or_create_session('test', 0)

# Test 1: Hello
r = pipeline.flow_engine.process_turn(session, "hello")
print(f'1. "hello" -> AI: {r[:60]}...')

# Test 2: Name
r = pipeline.flow_engine.process_turn(session, "My name is Priya")
print(f'2. "My name is Priya" -> AI: {r[:60]}...')

# Test 3: Billing issue
r = pipeline.flow_engine.process_turn(session, "I have a billing problem")
print(f'3. "I have a billing problem" -> AI: {r[:60]}...')

# Test 4: More details
r = pipeline.flow_engine.process_turn(session, "My bill is too high this month, it happened on January 15th")
print(f'4. "My bill is too high..." -> AI: {r[:60]}...')

# Test 5: Confirmation
r = pipeline.flow_engine.process_turn(session, "yes")
print(f'5. "yes" -> AI: {r[:60]}...')

# Test 6: Thank you
r = pipeline.flow_engine.process_turn(session, "thanks")
print(f'6. "thanks" -> AI: {r[:60]}...')

# Test 7: New conversation (should reset)
session2 = pipeline.get_or_create_session('test2', 0)
r = pipeline.flow_engine.process_turn(session2, "hello")
print(f'7. New conv "hello" -> AI: {r[:60]}...')

r = pipeline.flow_engine.process_turn(session2, "my name is Raj")
print(f'8. New conv "my name is Raj" -> AI: {r[:60]}...')

r = pipeline.flow_engine.process_turn(session2, "thank you")
print(f'9. New conv "thank you" -> AI: {r[:60]}...')

print()
print('=== Session 1 Info ===')
s1 = session.get_summary()
print(f'Phase: {s1["phase"]}')
print(f'Collected: {s1["collected_info"]}')
print(f'Turns: {s1["total_turns"]}')

print()
print('=== Session 2 Info ===')
s2 = session2.get_summary()
print(f'Phase: {s2["phase"]}')
print(f'Collected: {s2["collected_info"]}')
print(f'Turns: {s2["total_turns"]}')

pipeline.end_session('test')
pipeline.end_session('test2')