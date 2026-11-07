from django.urls import include, path

urlpatterns = [
    path("", include("webapp.ui.urls")),
]

handler404 = "webapp.ui.views.handler404"