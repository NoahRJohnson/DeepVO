"""Generate batches for training.

A batch consists of samples, where each sample is a sub-sequence. A sub-
sequence contains batch_size frames. Ground truth for the subsequence is
modified so that translations and rotations are relative to the first
frame of the sub-sequence rather than the first frame of the full
sequence. Rotation matrices are converted to Euler angles.
"""

import math
import numpy as np
import os
import random

from numpy.linalg import inv
from odometry import odometry
from os.path import join


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
        pose:  The 4x4 rotation-translation matrix representing the vehicle's
        pose.

    Returns:
        The pose represented as a vector.

        I.e. a (roll, pitch, yaw, lat, lng, alt) numpy array.
    """
    return np.concatenate((rotation_matrix_to_euler_angles(pose[:3, :3]),
                          pose[:3, 3]))


def process_poses(poses):
    """Fully convert subsequence of poses."""
    rectified_poses = rectify_poses(poses)
    return np.array([mat_to_pose_vector(pose) for pose in rectified_poses])


def get_stacked_rgbs(dataset, batch_frames):
    """Return list of dstacked rbg images."""
    rgbs = [np.array(left_cam) for left_cam, _ in dataset.rgb]
    mean_rgb = sum(rgbs) / float(batch_frames)
    rgbs = [rgb - mean_rgb for rgb in rgbs]
    return [np.dstack((frame1, frame2))
            for frame1, frame2 in zip(rgbs, rgbs[1:])]


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
    poses = dataset.poses
    x = np.array([np.vstack(get_stacked_rgbs(dataset))])
    y = process_poses(poses)
    return {'x': x, 'y': y}


def read_flow(name):
    """Open .flo file as np array.

    Args:
        name: string path to file

    Returns:
        Flat numpy array
    """
    f = open(name, 'rb')

    header = f.read(4)
    if header.decode("utf-8") != 'PIEH':
        raise Exception('Flow file header does not contain PIEH')

    width = np.fromfile(f, np.int32, 1).squeeze()
    height = np.fromfile(f, np.int32, 1).squeeze()

    flow = np.fromfile(f, np.float32, width * height * 2)\
             .reshape((height, width, 2))\
             .astype(np.float32)

    return flow


class Epoch():
    """Create batches of sub-sequences.

    Divide all train sequences into subsequences
    and yield batches of subsequences without repetition
    until all subsequences have been exhausted.
    """

    def __init__(self, datadir, flowdir, train_seq_nos,
                 window_size, step_size, batch_size):
        """Initialize.

        Args:
            datadir: The directory where the kitti `sequences` folder
                     is located.
            flowdir: The directory where the flownet images are
            train_seq_nos: list of strings corresponding to kitti
                           sequences in the training set
            window_size: Number of flow images per window in sequence
                         partitioning, i.e. subsequence length.
            step_size: int. Step size for sliding window in sequence
                       partitioning
            batch_size: Number of samples (subsequences) per batch.
                        Final batch may be smaller if batch_size is
                        greater than the number of subsequences remaining
                        when get_batch() is called.
        """
        if step_size > window_size:
            print("WARNING: step_size greater than window size. "
                  "This will result in unseen sequence frames.")

        self.datadir = datadir
        self.flowdir = flowdir
        self.train_seq_nos = train_seq_nos
        self.window_size = window_size
        self.step_size = step_size
        self.batch_size = batch_size
        self.window_idxs = []

        self.partition_sequences()

    def is_complete(self):
        """Stop serving batches if there are no more unused subsequences."""
        if len(self.window_idxs) > 0:
            return False
        else:
            return True

    def partition_sequences(self):
        """Partition a sequence into subsequences.

        Create subsequences of length window_size, with starting indices
        staggered by step_size.

        NOTE: The final subsequence may need to be padded to be the same
              length as all the others, if the arithmetic doesn't work
              out nicely.
        ALSO: self.step_size > self.window_size will result in flow
              samples from the full sequence failing to appear in
              the epoch.
        """
        for seq_no in self.train_seq_nos:
            len_seq = len(os.listdir(join(self.flowdir, seq_no)))
            for window_start in range(1, len_seq - self.window_size + 1,
                                      self.step_size):

                # Don't give window bounds with upper bound greater than
                # the number of actual frames in the sequence. Buffering
                # is handled in get_sample() for short final sub-sequence.
                window_end = min(window_start + self.window_size + 1,
                                 len_seq + 1)
                self.window_idxs.append((seq_no, (window_start, window_end)))
        random.shuffle(self.window_idxs)

    def get_sample(self, window_idx):
        """Create one sample.

        Create one window_size long subsequence.

        Args:
            windox_idx: (seq_no, (start_frame, end_frame + 1))

        Returns:
            (x, y):
                x: A (window_size, HxWx3) array of flownet image pixels
                y: A (window_size, 6) array of ground truth poses
        """
        buff = False
        seq, window_bounds = window_idx

        seq_path = join(self.flowdir, seq)

        frame_nos = range(*(window_bounds))
        if len(frame_nos) < self.window_size:
            missing_frames = range(self.window_size - len(frame_nos))
            buff = True

        x = [read_flow(join(seq_path,
                            "{i}.flo".format(i=frame_no)))
             for frame_no in frame_nos]

        if buff:
            img_size = x[0].shape
            x = x + [np.zeros(img_size) for i in missing_frames]

        x = np.array(x)

        raw_poses = odometry(self.datadir, seq, frames=frame_nos).poses
        y = process_poses(raw_poses)

        if buff:
            for i in missing_frames:
                y = np.vstack(y, np.zeros(6))
        return (x, y)

    def get_batch(self):
        """Get a batch.

        Returns:
            (x, y):
                x: A (batch_size, window_size, HxWx3) np array of subsequences.
                y: A (batch_size, window_size, HxWx3) np array of ground truth
                   pose vectors.
        NOTE: See __init__ docstring note about batch_size.
        """
        x = []
        y = []
        for sample in range(self.batch_size):
            if not self.is_complete():
                window_idx = self.window_idxs.pop()
                sample_x, sample_y = self.get_sample(window_idx)
                x.append(sample_x)
                y.append(sample_y)
        x = np.array(x)
        y = np.array(y)
        return (x, y)
