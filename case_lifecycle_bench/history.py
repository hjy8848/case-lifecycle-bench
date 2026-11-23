"""Full prior-session context for the memory comparison."""

from copy import deepcopy


class FullHistory:
    def wrap(self, agent):
        """Wrap one case's agent with its previously observed transcript."""
        history = []

        def wrapped(session, call):
            prior = {
                key: session[key]
                for key in ("t", "trigger_type", "trigger_id", "channel")
                if key in session
            }
            records = prior["records"] = []
            is_visit = session["trigger_type"] == "user_visit"
            if is_visit:
                records.append({"role": "user", "text": session["message"]})

            def remember_call(tool, **args):
                arguments = deepcopy(args)
                result = call(tool, **args)
                if tool == "say":
                    records.append({"role": "agent", "text": arguments["text"]})
                elif is_visit and tool == "user_reply":
                    records.append({"role": "user", "text": arguments["text"]})
                else:
                    records.append(
                        {"tool": tool, "args": arguments, "result": deepcopy(result)}
                    )
                return result

            try:
                return agent(
                    {**session, "full_history": deepcopy(history)}, remember_call
                )
            finally:
                # The environment can end a session by raising its step-limit signal.
                history.append(prior)

        return wrapped
