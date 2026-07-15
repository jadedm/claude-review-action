## Review Instructions

Analyze the code and respond with a JSON object:
{
  "summary": "1-3 sentence overview",
  "issues": [
    {
      "file": "path/to/file",
      "line": 42,
      "severity": "critical|high|medium|low",
      "category": "security|bug|performance|logic|style|maintainability|architecture",
      "description": "What's wrong and why",
      "suggestion": "How to fix it"
    }
  ],
  "positives": ["Things done well"],
  "overall_assessment": "approve|comment|request_changes"
}

Focus on actionable findings. Skip trivial style issues. Be concise.
Respond ONLY with the JSON object. No markdown fences.
