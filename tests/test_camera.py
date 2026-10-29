from track_seeds.camera import mask_url, stream_url
from track_seeds.config import CameraSettings


def cam(**kw) -> CameraSettings:
    return CameraSettings(_env_file=None, **kw)


def test_credentials_are_escaped_into_url():
    url = stream_url(cam(url="rtsp://192.168.1.64:554/Streaming/Channels/102",
                         username="operator", password="p@ss:w#rd/1%"))
    assert url == "rtsp://operator:p%40ss%3Aw%23rd%2F1%25@192.168.1.64:554/Streaming/Channels/102"
    assert mask_url(url) == "rtsp://operator:***@192.168.1.64:554/Streaming/Channels/102"


def test_url_credentials_take_precedence():
    url = "rtsp://admin:x@10.0.0.2/stream"
    assert stream_url(cam(url=url, username="other", password="y")) == url


def test_non_network_sources_untouched():
    assert stream_url(cam(url="0", username="u", password="p")) == "0"
    assert stream_url(cam(url="/tmp/video.mp4", username="u", password="p")) == "/tmp/video.mp4"
