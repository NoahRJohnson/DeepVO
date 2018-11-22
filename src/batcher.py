"""Generate batches for training.

A batch is of size one, meaning one sub-sequence. A sub-sequence contains
batch_size frames. Ground truth for the subsequence is modified so that
translations and rotations are relative to the first frame of the sub-sequence
rather than the first frame of the full sequence. Rotation matrices are
converted to Euler angles.
"""

import numpy as np
from odometry import odometry
from PIL import Image
import random
from numpy.linalg import inv
import math

from os import listdir
from os.path import isfile, join


def get_seq_total_frames(seq, basedir):
    """Get the total number of frames in a KITTI sequence, 0-indexed.

    It's inefficient to pull the whole dataset only to grab a few
    frames for a batch, so we'll count the number of total frames
    before we pick an initial frame, then only pull in the desired
    frames.

    Args:
        seq: The KITTI sequence number to examine.
        basedir: The directory where KITTI data is stored.

    Returns:
        The 0-indexed count of how many image frames there are in
        this KITTI sequence.
    """
    path = basedir + "sequences/" + str(seq) + "/image_2/"

    # This actually returns the index of the last frame rather than
    # the number of frames, for convenience
    return len([f for f in listdir(path) if isfile(join(path, f))]) - 1


def is_rotation_matrix(r):
    """Check if a matrix is a valid rotation matrix.

    referred from https://www.learnopencv.com/rotation-matrix-to-euler-angles/
    """
    rt = np.transpose(r)
    should_be_identity = np.dot(rt, r)
    i = np.identity(3, dtype=r.dtype)
    n = np.linalg.norm(i - should_be_identity)
    return n < 1e-6


def rotation_matrix_to_euler_angles(r):
    """Convert rotation matrix to euler angles.

    referred from https://www.learnopencv.com/rotation-matrix-to-euler-angles
    """
    assert(is_rotation_matrix(r))
    sy = math.sqrt(r[0, 0] * r[0, 0] + r[1, 0] * r[1, 0])
    singular = sy < 1e-6

    if not singular:
        x = math.atan2(r[2, 1], r[2, 2])
        y = math.atan2(-r[2, 0], sy)
        z = math.atan2(r[1, 0], r[0, 0])
    else:
        x = math.atan2(-r[1, 2], r[1, 1])
        y = math.atan2(-r[2, 0], sy)
        z = 0

    return np.array([x, y, z])


def rectify_poses(poses):
    """Set ground truth relative to first pose in subsequence.

    Poses are rotation-translation matrices relative to the first
    pose in the full sequence. To get meaningful output from sub-
    sequences, we need to alter them to be relative to the
    first position in the sub-sequence.

    Args:
        poses:  An iterable of 4x4 rotation-translation matrices representing 
                the vehicle's pose at each step in the sequence.

    Returns:
        An iterable of rectified rotation-translation matrices
    """
    first_frame = poses[0]
    rectified_poses = [np.dot(inv(first_frame), x) for x in poses[1:]]
    return rectified_poses


def mat_to_pose_vector(pose):
    """Convert the 4x4 rotation-translation matrix into a 6-dim vector.

    Args:
        pose:  The 4x4 rotation-translation matrix representing the vehicle's pose.

    Returns:
        The pose represented as a vector.

        I.e. a (roll, pitch, yaw, lat, lng, alt) numpy array.
    """
    return np.concatenate((rotation_matrix_to_euler_angles(pose[:3, :3]),
                          pose[:3, 3]))


def process_poses(dataset):
    """Fully convert subsequence of poses."""
    poses = dataset.poses
    rectified_poses = rectify_poses(poses)
    return [mat_to_pose_vector(pose) for pose in rectified_poses]


def get_stacked_rgbs(dataset, batch_frames):
    """Return list of dstacked rbg images."""
    rgbs = [np.array(left_cam) for left_cam, _ in dataset.rgb]
    mean_rgb = sum(rgbs) / float(batch_frames)
    rgbs = [rgb - mean_rgb for rgb in rgbs]
    return [np.dstack((frame1, frame2))
            for frame1, frame2 in zip(rgbs, rgbs[1:])]


def batcher(basedir, batch_frames, train_seqs):
    """
    Creates one batch.

    Args:
        basedir: The directory where KITTI data is stored.
        batch_frames: The number of image pairs in the batch.
        train_seqs: 

    Returns:
        A batch of the form

        {'x': x, 'y': y}

        for consumption by Keras, where x is data and y is labels.
    """
    # Select a sequence at random.
    sequence = random.choice(train_seqs)

    max_frame = get_seq_total_frames(sequence)
    first_frame = random.randint(0, max_frame - batch_frames + 1)
    last_frame = first_frame + batch_frames

    # Load the data. Specify the frame range to load.
    dataset = odometry(basedir,
                       sequence,
                       frames=range(first_frame, last_frame))

    x = np.array([np.vstack(get_stacked_rgbs(dataset, batch_frames))])
    y = process_poses(dataset)

    return {'x': x, 'y': y}


def test_batch(basedir, seq):
    """Process images and ground truth for a test sequence.

    Args:
        basedir: The directory where KITTI data is stored.
        seq: The KITTI sequence number to test.

    Returns:
        A batch of the form

        {'x': x, 'y': y}

        for consumption by Keras, where x is data and y is labels.
    """
    dataset = odometry(basedir, seq)
    x = np.array([np.vstack(get_stacked_rgbs(dataset))])
    y = process_poses(dataset)
    return {'x': x, 'y': y}
