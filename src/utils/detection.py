# Copyright 2022 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================
"""Utils."""

from itertools import product
import math

import numpy as np


def prior_box(image_sizes, min_sizes, steps, clip=False):
    """
    Generate candidate boxes on feature graphs of different sizes.

    Args:
        image_sizes (tuple): A tuple represents (image_width,image_height) .
        min_sizes (list): Size of prior boxes corresponding to different feature layers, which shape is [N,M], where N
            represents the number of feature layers, M represents different kind of sizes in the layer.
        steps (list): Multiple by which each feature layer is compressed, which length is N, represents the different
            multiple of N layers.
        clip (bool): Whether to restrict the output between 0 and 1.

    Returns:
        A numpy ndarray with shape [N,4], which represents generated N prior boxes with x,y,width and height.
    """
    feature_maps = [
        [math.ceil(image_sizes[0] / step), math.ceil(image_sizes[1] / step)]
        for step in steps]

    anchors = []
    for k, f in enumerate(feature_maps):
        for i, j in product(range(f[0]), range(f[1])):
            for min_size in min_sizes[k]:
                s_kx = min_size / image_sizes[1]
                s_ky = min_size / image_sizes[0]
                cx = (j + 0.5) * steps[k] / image_sizes[1]
                cy = (i + 0.5) * steps[k] / image_sizes[0]
                anchors += [cx, cy, s_kx, s_ky]

    output = np.asarray(anchors).reshape([-1, 4]).astype(np.float32)

    if clip:
        output = np.clip(output, 0, 1)

    return output


def center_point2boxes(boxes):
    """
    Convert the box coordinate format from x,y,w,h to x1,y1,x2,y2.

    Args:
        boxes (numpy.ndarray): Which shape is [N,4], represents x,y,width,height of N boxes.

    Returns:
        A numpy ndarray with shape [N,4], which represents N boxes with x1,y1,x2 and y2(Top left and bottom right of
            boxes).
    """
    return np.concatenate((boxes[:, 0:2] - boxes[:, 2:4] / 2,
                           boxes[:, 0:2] + boxes[:, 2:4] / 2), axis=1)


def compute_intersect(a, b):
    """
    Compute intersection area of a and b.

    Args:
        a (numpy.ndarray): Which shape is [N,4], represents x1,y1,x2,y2(Top left and bottom right corner) of N boxes.
        b (numpy.ndarray): Which shape is [M,4], represents x1,y1,x2,y2(Top left and bottom right corner) of M boxes.

    Returns:
        A numpy ndarray with shape [N,M], means each box of a calculate intersection area size with each box of b.
    """
    a_count = a.shape[0]
    b_count = b.shape[0]
    max_xy = np.minimum(
        np.broadcast_to(np.expand_dims(a[:, 2:4], 1), [a_count, b_count, 2]),
        np.broadcast_to(np.expand_dims(b[:, 2:4], 0), [a_count, b_count, 2]))
    min_xy = np.maximum(
        np.broadcast_to(np.expand_dims(a[:, 0:2], 1), [a_count, b_count, 2]),
        np.broadcast_to(np.expand_dims(b[:, 0:2], 0), [a_count, b_count, 2]))
    inter = np.maximum((max_xy - min_xy), np.zeros_like(max_xy - min_xy))
    return inter[:, :, 0] * inter[:, :, 1]


def compute_overlaps(a, b):
    """
    Compute IoU between a and b.

    Args:
        a (numpy.ndarray): Which shape is [N,4], represents x1,y1,x2,y2(Top left and bottom right corner) of N boxes.
        b (numpy.ndarray): Which shape is [M,4], represents x1,y1,x2,y2(Top left and bottom right corner) of M boxes.

    Returns:
        A numpy ndarray with shape [N,M], means each box of a calculate intersection_area_size/union_area_size with each
         box of b.
    """
    inter = compute_intersect(a, b)
    area_a = np.broadcast_to(
        np.expand_dims(
            (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]), 1),
        np.shape(inter))
    area_b = np.broadcast_to(
        np.expand_dims(
            (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1]), 0),
        np.shape(inter))
    union = area_a + area_b - inter
    return inter / union


def match(threshold, boxes, priors, var, labels, landms):
    """
    Prior box matching function. During the training, it is necessary to determine which prior frame matches the
    ground truth in the training image first, and the boundary frame corresponding to the matched prior frame will
    be responsible for predicting it. There are two matching principles between prior frame and ground truth. The
    first is that for each ground truth in the image, find the prior box with the largest IoU of it,which matches with
    it, so that each ground truth must match with a prior. Second, for the remaining unmatched prior boxes, if a ground
    truth and its IOU are greater than a certain threshold (generally set to 0.5), then change prior and the ground
    truth, The remaining prior frames that are not matched are all negative samples (if the IOU of multiple ground
    truths and a prior frame are all greater than the threshold, prior will only match the one with the largest IOU).

    Args:
        threshold (float): IoU threshold, decide whether set to background.
        boxes (numpy.ndarray): Ground truth boxes, whose shape is [N,4] represents x1,y1,x2,y2 of N boxes.
        priors (numpy.ndarray): Prior boxes, whose shape is [M,4] represents x,y,w,h of M boxes.
        var (list): Variance of priors.
        labels (numpy.ndarray): Label of boxes.
        landms (numpy.ndarray): Landmarks correspond to boxes, whose shape is [N,10], represents 5 pair of (x,y) for
            N boxes.

    Returns:
        A tuple,represents matched and encoded boxes, confidence and landmarks.
    """
    centerbox = center_point2boxes(priors)
    overlaps = compute_overlaps(boxes, centerbox)

    # The prior box that overlaps most with the annotation box
    best_prior_overlap = overlaps.max(1, keepdims=True)
    best_prior_idx = np.argsort(-overlaps, axis=1)[:, 0:1]

    valid_gt_idx = best_prior_overlap[:, 0] >= 0.2
    best_prior_idx_filter = best_prior_idx[valid_gt_idx, :]
    if best_prior_idx_filter.shape[0] <= 0:
        loc = np.zeros((priors.shape[0], 4), dtype=np.float32)
        conf = np.zeros((priors.shape[0],), dtype=np.int32)
        landm = np.zeros((priors.shape[0], 10), dtype=np.float32)
        return loc, conf, landm

    # The closest annotation box of each prior box
    best_truth_overlap = overlaps.max(0, keepdims=True)
    best_truth_idx = np.argsort(-overlaps, axis=0)[:1, :]

    best_truth_idx = best_truth_idx.squeeze(0)
    best_truth_overlap = best_truth_overlap.squeeze(0)
    best_prior_idx = best_prior_idx.squeeze(1)
    best_prior_idx_filter = best_prior_idx_filter.squeeze(1)
    best_truth_overlap[best_prior_idx_filter] = 2

    for j in range(best_prior_idx.shape[0]):
        best_truth_idx[best_prior_idx[j]] = j

    matches = boxes[best_truth_idx]

    # encode boxes
    offset_cxcy = (matches[:, 0:2] + matches[:, 2:4]) / 2 - priors[:, 0:2]
    offset_cxcy /= (var[0] * priors[:, 2:4])
    wh = (matches[:, 2:4] - matches[:, 0:2]) / priors[:, 2:4]
    wh[wh == 0] = 1e-12
    wh = np.log(wh) / var[1]
    loc = np.concatenate([offset_cxcy, wh], axis=1)

    conf = labels[best_truth_idx]
    conf[best_truth_overlap < threshold] = 0

    matches_landm = landms[best_truth_idx]

    # encode landms
    matched = np.reshape(matches_landm, [-1, 5, 2])
    priors = np.broadcast_to(np.expand_dims(priors, 1), [priors.shape[0], 5, 4])
    offset_cxcy = matched[:, :, 0:2] - priors[:, :, 0:2]
    offset_cxcy /= (priors[:, :, 2:4] * var[0])
    landm = np.reshape(offset_cxcy, [-1, 10])

    return loc, np.array(conf, dtype=np.int32), landm


class BboxEncoder:
    """
    For the input annotation box data, generate the offset and variance between each prior box and key point data and
    the most matched annotation box for network training.

    Args:
        config (dict): A dictionary contains some configuration for dataset,should contains:
            config['image_size']: scaled image size adopted by the training network
            config['match_thresh']: rate threshold of prior box and annotation box
            config['variance']: pre-set value,is used to decode the prior box to prediction box
            config['clip']: Whether the width, height and coordinates of prior boxes are guaranteed to be between
             0 and 1 when generating.

    Inputs:
        image: An image, which shape is (C, H, W).


    Outputs:
        A tuple whose first element is the input image data and the second to forth elements are the adjusted
        bounding box, confidence and landmark.
    """

    def __init__(self, config):
        self.match_thresh = config['match_thresh']
        self.variances = config['variance']
        self.priors = prior_box((config['image_size'], config['image_size']),
                                [[16, 32], [64, 128], [256, 512]],
                                [8, 16, 32],
                                config['clip'])

    def __call__(self, image, targets):
        boxes = targets[:, :4]
        labels = targets[:, -1]
        landms = targets[:, 4:14]
        priors = self.priors

        loc_t, conf_t, landm_t = match(self.match_thresh, boxes, priors, self.variances, labels, landms)

        return image, loc_t, conf_t, landm_t


def decode_bbox(bbox, priors, var):
    """
    The coordinates of the adjusted bounding box are calculated by adjusting the prior with offset and variance
    returns data in the format of (x0,y0,x1,y1).

    Args:
        bbox (np.ndarray): Predict box information get from RetinaFace network forward pass.
        priors (np.ndarray): Prior boxes correspond to predict information, need to be adjusted.
        var (list): Variance of priors.

    Returns:
        A numpy ndarray, whose shape is [N,4], represents top left and bottom right point coordinates of N boxes.
    """
    boxes = np.concatenate((
        priors[:, 0:2] + bbox[:, 0:2] * var[0] * priors[:, 2:4],

        # (xc, yc, w, h)
        priors[:, 2:4] * np.exp(bbox[:, 2:4] * var[1])), axis=1)

    # (x0, y0, w, h)
    boxes[:, :2] -= boxes[:, 2:] / 2

    # (x0, y0, x1, y1)
    boxes[:, 2:] += boxes[:, :2]
    return boxes


def decode_landm(landm, priors, var):
    """
    The coordinates of the adjusted landmarks are calculated by adjusting the prior with offset and variance
    returns data in the format of (x0,y0,x1,y1,x2,y2,x3,y3,x4,y4).

    Args:
        landm (np.ndarray): Predict landmark information get from RetinaFace network forward pass.
        priors (np.ndarray): Prior boxes correspond to predict information, need to be adjusted.
        var (list): Variance of priors.

    Returns:
        A numpy ndarray, whose shape is [N,10], represents 5 pairs of coordinate of N bounding boxes.
    """

    return np.concatenate((priors[:, 0:2] + landm[:, 0:2] * var[0] * priors[:, 2:4],
                           priors[:, 0:2] + landm[:, 2:4] * var[0] * priors[:, 2:4],
                           priors[:, 0:2] + landm[:, 4:6] * var[0] * priors[:, 2:4],
                           priors[:, 0:2] + landm[:, 6:8] * var[0] * priors[:, 2:4],
                           priors[:, 0:2] + landm[:, 8:10] * var[0] * priors[:, 2:4],
                           ), axis=1)
