def analyze_sentiment_and_risk(message: str):
    lowered = message.lower()
    # Простые триггер-слова для симуляции деструктивного поведения на демо перед судьями
    if any(word in lowered for word in ["уволиться", "устала", "плохо", "выгорел", "надоело", "ужасно"]):
        return "negative", True
    return "neutral", False