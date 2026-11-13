import os
import sys
import re
from pathlib import Path
from typing import Any
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from firebase_client import sign_in, sign_up
from llm import ColabLLMClient, OllamaLLMClient
from main import PipelineConnector

st.set_page_config(
    page_title="Intelligent Information Security Assistant",
    page_icon="🛡️",
    layout="wide",
)


@st.cache_resource(show_spinner="Initializing RetrievalSystem, BGE-M3 Reranker, and Local LLM...")
def init_rag_pipeline(backend_mode: str, model_name: str, colab_url: str) -> PipelineConnector:
    """Initialize and cache the end-to-end RAG pipeline once per server session."""
    if backend_mode == "Local Ollama":
        llm_client = OllamaLLMClient(model_name=model_name.strip() or "qwen2.5:3b")
    else:
        llm_client = ColabLLMClient(endpoint_url=colab_url.strip())

    return PipelineConnector(llm_client=llm_client)


def _to_dict(obj: Any) -> dict[str, Any]:
    """Safely convert dataclass or custom schema object to dictionary."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {}


def _render_assistant_metadata(msg: dict[str, Any]) -> None:
    """Render intent, faithfulness score, latency, rewritten query, and source provenance."""
    meta_badges: list[str] = []
    if msg.get("intent"):
        meta_badges.append(f"**Intent:** `{msg['intent']}`")

    faith = msg.get("faithfulness")
    verif_status = msg.get("verif_status")
    if faith is not None:
        pct = faith * 100.0 if faith <= 1.0 else float(faith)
        status_label = f" ({verif_status})" if verif_status else ""
        meta_badges.append(f"**Faithfulness:** `{pct:.1f}%`{status_label}")

    if msg.get("latency") is not None:
        meta_badges.append(f"**Latency:** `{msg['latency']:.2f}s`")

    if meta_badges:
        st.caption(" • ".join(meta_badges))

    if msg.get("rewritten_query"):
        st.caption(f"🔄 **Context Resolved:** Query rewritten as *{msg['rewritten_query']}*")

    citations = msg.get("citations") or []
    if citations:
        with st.expander(f"📚 Referenced Sources & Provenance ({len(citations)} sources)"):
            for item in citations:
                c = _to_dict(item)
                tag = c.get("citation_tag", "")
                source = c.get("source", "")
                doc_title = (
                    c.get("document_title")
                    or c.get("doc_title")
                    or c.get("title")
                    or "Security Document"
                )
                full_title = f"{source} - {doc_title}" if source and source not in doc_title else doc_title
                file_name = c.get("file_name", "")
                locator = c.get("locator_display") or c.get("lines") or ""
                heading = c.get("heading_path")
                heading_str = f" › {' > '.join(heading)}" if isinstance(heading, list) and heading else ""

                st.markdown(f"**{tag} {full_title}**{heading_str}")
                details = " · ".join(part for part in [f"`{file_name}`" if file_name else "", str(locator)] if part)
                if details:
                    st.caption(details)


# SESSION STATE INITIALIZATION 
if "user" not in st.session_state:
    st.session_state.user = None
if "messages" not in st.session_state:
    st.session_state.messages = []

st.title("🛡️ Intelligent Information Security Assistant")
st.caption("Modular RAG Workspace")

# AUTHENTICATION GATE (FIREBASE) 
if st.session_state.user is None:
    st.info("Please sign in to access the Intelligent Information Security Workspace.")
    tab_login, tab_signup = st.tabs(["Sign In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In")
            if submitted:
                if not email.strip() or not password.strip():
                    st.warning("Please enter both email and password.")
                else:
                    try:
                        st.session_state.user = sign_in(email.strip(), password)
                        st.session_state.messages = []
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

    with tab_signup:
        with st.form("signup_form"):
            new_email = st.text_input("New Email")
            new_password = st.text_input("New Password (min 6 characters)", type="password")
            reg_submitted = st.form_submit_button("Create Account")
            if reg_submitted:
                if not new_email.strip() or not new_password.strip():
                    st.warning("Please fill in all required fields.")
                else:
                    try:
                        sign_up(new_email.strip(), new_password)
                        st.success("Account created successfully! Switch to the Sign In tab to log in.")
                    except Exception as e:
                        st.error(str(e))
    st.stop()

# SIDEBAR CONTROLS 
with st.sidebar:
    st.markdown(f"**Signed in as:** `{st.session_state.user['email']}`")
    st.caption(f"UID: `{st.session_state.user['uid']}`")
    st.divider()

    st.subheader("Model Configuration")
    backend_mode = st.selectbox(
        "LLM Inference Backend",
        ["Local Ollama", "Remote Colab GPU (FastAPI)"],
        index=0,
    )

    if backend_mode == "Local Ollama":
        model_name = st.text_input(
            "Local Ollama Model",
            value="qwen2.5:3b",
            help="Ensure the model is pulled locally via 'ollama pull qwen2.5:3b'.",
        )
        colab_url = ""
    else:
        model_name = "Qwen2.5-3B-Instruct-colab-remote"
        colab_url = st.text_input(
            "Colab Tunnel URL",
            value=os.getenv("COLAB_API_URL", ""),
            help="Paste the active HTTPS tunnel URL from your Colab FastAPI notebook.",
        )

    st.divider()
    col_clear, col_logout = st.columns(2)
    with col_clear:
        if st.button("Clear Chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()
    with col_logout:
        if st.button("Sign Out", use_container_width=True):
            st.session_state.user = None
            st.session_state.messages = []
            st.rerun()

# LOAD CACHED PIPELINE 
try:
    pipeline = init_rag_pipeline(backend_mode, model_name, colab_url)
except Exception as e:
    st.error(f"Failed to initialize RAG pipeline: {e}")
    st.stop()

# RENDER CHAT HISTORY 
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("warning_banner"):
            st.warning(msg["warning_banner"], icon="⚠️")
        st.markdown(msg["content"])
        if msg["role"] == "assistant":
            _render_assistant_metadata(msg)

# HANDLE NEW USER QUERY 
query = st.chat_input("Ask about web vulnerabilities, OWASP standards, CWE mappings, or remediation...")

if query is not None:
    if not query.strip():
        st.warning("Please enter a non-empty question.")
    else:
        clean_query = query.strip()
        st.session_state.messages.append({"role": "user", "content": clean_query})
        with st.chat_message("user"):
            st.markdown(clean_query)

        with st.chat_message("assistant"):
            with st.spinner("Retrieving security evidence and verifying response faithfulness..."):
                try:
                    # Sync per-user session history into the pipeline before execution
                    if hasattr(pipeline, "reset_conversation"):
                        pipeline.reset_conversation()
                    elif hasattr(pipeline, "orchestrator") and hasattr(pipeline.orchestrator, "reset_conversation"):
                        pipeline.orchestrator.reset_conversation()

                    recent_history = [
                        {
                            "role": m["role"],
                            "content": m["content"].split("\n---\n")[0][:300],
                        }
                        for m in st.session_state.messages[:-1][-6:]
                    ]
                    pipeline.conversation_history = list(recent_history)
                    if hasattr(pipeline, "orchestrator"):
                        pipeline.orchestrator.conversation_memory = list(recent_history)

                    envelope = pipeline.ask(clean_query)
                    status = str(getattr(envelope, "status", "ok")).lower()

                    if status == "ok":
                        raw_md = getattr(envelope, "final_answer_markdown", "") or "No answer generated."
                        body_md = re.split(r"\n---\s*\n###\s*Danh mục tài liệu tham chiếu", raw_md, maxsplit=1)[0].strip()
                        warn_match = re.match(r"^>{1,3}\s*(.+?)(?:\n+|$)", body_md)
                        if warn_match:
                            warning_banner = warn_match.group(1).strip()
                            answer_text = body_md[warn_match.end():].strip()
                        else:
                            answer_text = body_md
                    elif status == "clarification":
                        clar_msg = getattr(envelope, "clarification_message", "") or "Please clarify your question."
                        answer_text = f"💡 **Clarification Needed:** {clar_msg}"
                    elif status in {"rejected", "out_of_scope"}:
                        reason = getattr(envelope, "reason", "") or "Request is outside the supported security scope."
                        answer_text = f"🚫 **Request Refused:** {reason}"
                    else:
                        fallback_md = getattr(envelope, "final_answer_markdown", "")
                        err_msg = getattr(envelope, "error_message", "") or getattr(envelope, "reason", "")
                        if fallback_md:
                            clean_fallback = re.sub(r"^>{1,3}\s*", "", fallback_md).strip()
                            warning_banner = clean_fallback
                            answer_text = ""
                        else:
                            answer_text = f"⚠️ **System Notice:** {err_msg or 'Unable to generate a verified answer.'}"

                    if warning_banner:
                        st.warning(warning_banner, icon="⚠️")
                    if answer_text:
                        st.markdown(answer_text)

                    q_plan = _to_dict(getattr(envelope, "query_plan", None))
                    verif = _to_dict(getattr(envelope, "verification", None))
                    meta = _to_dict(getattr(envelope, "metadata", None))

                    rewritten_q = q_plan.get("resolved_query") if q_plan.get("context_used") else None
                    raw_verif_status = str(verif.get("status", "")).replace("VerificationStatus.", "")

                    assistant_record = {
                        "role": "assistant",
                        "content": answer_text,
                        "intent": q_plan.get("intent") or getattr(envelope, "intent", None),
                        "faithfulness": verif.get("faithfulness_score"),
                        "verif_status": raw_verif_status if raw_verif_status else None,
                        "latency": meta.get("total_latency_s"),
                        "rewritten_query": rewritten_q,
                        "citations": getattr(envelope, "citations", []) or [],
                    }
                    _render_assistant_metadata(assistant_record)
                    st.session_state.messages.append(assistant_record)

                except Exception as e:
                    st.error(f"Pipeline Execution Error: {e}")