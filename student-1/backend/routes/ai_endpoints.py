# AI-assisted endpoints
# Creation date: 31/08/2026

from flask import Blueprint, request

try:
    from backend.responses import ok
    from backend.services import summary as summary_service
    from backend.services.ollama_client import AIUnavailable
except ImportError:  # pragma: no cover - supports local execution
    from responses import ok
    from services import summary as summary_service
    from services.ollama_client import AIUnavailable

bp = Blueprint('ai', __name__, url_prefix='/api/ai')


@bp.route('/summary', methods=['POST'])
def get_summary():
    payload = request.get_json(silent=True) or {}
    text = payload.get('text') or payload.get('notes')

    if text is None or str(text).strip() == '':
        return ok({'error': 'No text provided'}, 400)

    try:
        result = summary_service.summary(str(text))
        return ok({'summary': result})
    except AIUnavailable as exc:
        return ok({'error': str(exc), 'summary': ''}, 503)
    except Exception as exc:  # pragma: no cover - defensive fallback
        return ok({'error': f'Summary generation failed: {exc}', 'summary': ''}, 500)