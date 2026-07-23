from flask import Blueprint, render_template

bp = Blueprint("preferences", __name__)


@bp.route("/preferences")
async def preferences_page():
    return render_template("preferences.html")
