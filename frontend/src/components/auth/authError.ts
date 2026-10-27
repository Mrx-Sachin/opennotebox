import { AxiosError } from "axios"

/** Turn an axios failure into an accurate, user-facing message.
    Previously every failure - bad credentials, the API being down, a 500 -
    rendered as "Invalid username or password", which is wrong and actively
    misleading when the real problem is "the server isn't reachable". */
export function describeAuthError(err: unknown, context: "login" | "register"): string {
  const axErr = err as AxiosError<{ detail?: string }>
  if (!axErr?.response) {
    // No response at all: request never completed - network down, API
    // unreachable, CORS, or a timeout. Not a credentials problem.
    return "Can't reach the server. Check that it's running and your connection, then try again."
  }
  const { status, data } = axErr.response
  if (status === 401) {
    return "Incorrect username or password."
  }
  if (status === 400 && context === "register") {
    // Backend returns a specific detail here (e.g. "Username already taken")
    // for validation failures - surface it instead of a generic message.
    return data?.detail || "That username or password isn't valid."
  }
  if (status === 403) {
    return data?.detail || "Registration is closed on this server."
  }
  if (status === 422) {
    return "Please check the fields and try again."
  }
  if (status >= 500) {
    return "The server hit an error. Please try again in a moment."
  }
  return data?.detail || "Something went wrong. Please try again."
}
