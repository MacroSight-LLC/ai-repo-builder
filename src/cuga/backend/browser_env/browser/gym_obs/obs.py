# Copyright 2024 ServiceNow
# Modifications Copyright 2025 CUGA
# Licensed under the Apache License, Version 2.0

import logging
import pkgutil
import time

import numpy as np
import playwright
from browsergym.core.observation import (
    MarkingError,
    extract_dom_extra_properties,
    extract_dom_snapshot,
    extract_focused_element_bid,
    extract_merged_axtree,
    extract_screenshot,
)
from playwright.async_api import Page
from playwright.sync_api import BrowserContext

EXTRACT_OBS_MAX_TRIES = 5
BID_ATTR = "bid"
logger = logging.getLogger(__name__)


def _pre_extract(page: playwright.sync_api.Page):
    """
    pre-extraction routine, marks dom elements (set bid and dynamic attributes like value and checked)
    """
    js_frame_mark_elements = pkgutil.get_data(__name__, "javascript/frame_mark_elements.js").decode("utf-8")

    # we can't run this loop in JS due to Same-Origin Policy
    # (can't access the content of an iframe from a another one)
    def mark_frames_recursive(frame, frame_bid: str):
        assert frame_bid == "" or (frame_bid.islower() and frame_bid.isalpha())

        # mark all DOM elements in the frame (it will use the parent frame element's bid as a prefix)
        warning_msgs = frame.evaluate(
            js_frame_mark_elements,
            [frame_bid, BID_ATTR],
        )
        # print warning messages if any
        for msg in warning_msgs:
            logger.warning(msg)

        # recursively mark all descendant frames
        for child_frame in frame.child_frames:
            # deal with detached frames
            if child_frame.is_detached():
                continue
            # deal with weird frames (pdf viewer in <embed>)
            child_frame_elem = child_frame.frame_element()
            if not child_frame_elem.content_frame() == child_frame:
                logger.warning(f"Skipping frame '{child_frame.name}' for marking, seems problematic.")
                continue
            # deal with sandboxed frames with blocked script execution
            sandbox_attr = child_frame_elem.get_attribute("sandbox")
            if sandbox_attr is not None and "allow-scripts" not in sandbox_attr.split():
                continue
            child_frame_bid = child_frame_elem.get_attribute(BID_ATTR)
            if child_frame_bid is None:
                raise MarkingError("Cannot mark a child frame without a bid.")
            mark_frames_recursive(child_frame, frame_bid=child_frame_bid)

    # mark all frames recursively
    mark_frames_recursive(page.main_frame, frame_bid="")


def _post_extract(page: playwright.sync_api.Page):
    js_frame_unmark_elements = pkgutil.get_data(__name__, "javascript/frame_unmark_elements.js").decode(
        "utf-8"
    )

    # we can't run this loop in JS due to Same-Origin Policy
    # (can't access the content of an iframe from a another one)
    for frame in page.frames:
        if not frame == page.main_frame:
            # deal with weird frames (pdf viewer in <embed>)
            if not frame.frame_element().content_frame() == frame:
                logger.warning(f"Skipping frame '{frame.name}' for unmarking, seems problematic.")
                continue
            # deal with sandboxed frames with blocked script execution
            sandbox_attr = frame.frame_element().get_attribute("sandbox")
            if sandbox_attr is not None and "allow-scripts" not in sandbox_attr.split():
                continue

        try:
            frame.evaluate(js_frame_unmark_elements)
        except playwright.sync_api.Error as e:
            if "Frame was detached" in str(e):
                pass
            else:
                raise


class GymObs:
    def __init__(self, page: Page, context: BrowserContext):
        self.page = page
        self.context = context
        pass

    def get_obs(self):
        for retries_left in reversed(range(EXTRACT_OBS_MAX_TRIES)):
            try:
                # pre-extraction, mark dom elements (set bid, set dynamic attributes like value and checked)
                _pre_extract(self.page)

                dom = extract_dom_snapshot(self.page)
                axtree = extract_merged_axtree(self.page)
                focused_element_bid = extract_focused_element_bid(self.page)
                extra_properties = extract_dom_extra_properties(dom)
            except (playwright.sync_api.Error, MarkingError) as e:
                err_msg = str(e)
                # try to add robustness to async events (detached / deleted frames)
                if retries_left > 0 and (
                    "Frame was detached" in err_msg
                    or "Frame with the given frameId is not found" in err_msg
                    or "Execution context was destroyed" in err_msg
                    or "Frame has been detached" in err_msg
                    or "Cannot mark a child frame without a bid" in err_msg
                ):
                    logger.warning(
                        f"An error occured while extracting the dom and axtree. Retrying ({retries_left}/{EXTRACT_OBS_MAX_TRIES} tries left).\n{repr(e)}"
                    )
                    # post-extract cleanup (aria-roledescription attribute)
                    _post_extract(self.page)
                    time.sleep(0.5)
                    continue
                else:
                    raise
            break

            # post-extraction cleanup of temporary info in dom
        _post_extract(self.page)

        # use first user message as goal, if any
        # use all user images before first user message as goal images, if any

        # obs is generic to all tasks
        obs = {
            "open_pages_urls": [page.url for page in self.context.pages],
            "active_page_index": np.asarray([self.context.pages.index(self.page)]),
            "url": self.page.url,
            "screenshot": extract_screenshot(self.page),
            "dom_object": dom,
            "axtree_object": axtree,
            "extra_element_properties": extra_properties,
            "focused_element_bid": focused_element_bid,
        }

        return obs
