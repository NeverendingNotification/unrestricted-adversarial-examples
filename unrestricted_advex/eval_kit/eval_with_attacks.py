"""Evaluate a model with attacks."""

import numpy as np
import tcu_images
import tensorflow as tf
import torch
import torchvision
from cleverhans.attacks import SPSA
from cleverhans.model import Model
from six.moves import xrange


class CleverhansModelWrapper(Model):
  def __init__(self, model_fn):
    """
    Wrap a callable function that takes a numpy array of shape (N, C, H, W),
    and outputs a numpy vector of length N, with each element in range [0, 1].
    """
    self.nb_classes = 2

    def two_class_model_fn(x):
      class_one_logit = model_fn(x)
      class_zero_logit = -class_one_logit
      stack = np.vstack([class_zero_logit, class_one_logit]).T
      return stack

    self.model_fn = two_class_model_fn

  def fprop(self, x, **kwargs):
    logits_op = tf.py_func(self.model_fn, [x], tf.float32)
    return {'logits': logits_op}


def spsa_attack(model, batch_nchw, labels, epsilon=(4. / 255)):
  with tf.Graph().as_default():
    # Prepare graph
    x_input = tf.placeholder(tf.float32, shape=(1,) + batch_nchw.shape[1:])
    y_label = tf.placeholder(tf.int32, shape=(1,))

    cleverhans_model = CleverhansModelWrapper(model)
    attack = SPSA(cleverhans_model)

    x_adv = attack.generate(
      x_input,
      y=y_label,
      epsilon=epsilon,
      num_steps=30,
      early_stop_loss_threshold=-1.,
      spsa_samples=32,
      is_debug=True)

    # Run computation
    all_x_adv_np = []
    with tf.Session() as sess:
      for i in xrange(len(batch_nchw)):
        x_adv_np = sess.run(x_adv, feed_dict={
          x_input: np.expand_dims(batch_nchw[i], axis=0),
          y_label: np.expand_dims(labels[i], axis=0),
        })
        all_x_adv_np.append(x_adv_np)
    return np.concatenate(all_x_adv_np)




def null_attack(model, x_np, y_np):
  return x_np


def run_attack(model, data_iter, attack_fn, max_num_batches=1):
  """
  :param model: Model should output a
  :param data_iter:
  :param attack:
  :param max_num_batches:
  :return: (logits, labels)
  """
  all_labels = []
  all_logits = []
  for i_batch, (x_np, y_np) in enumerate(data_iter):
    if max_num_batches > 0 and i_batch >= max_num_batches:
      break

    x_adv = attack_fn(model, x_np, y_np)
    logits = model(x_adv)
    all_labels.append(y_np)
    all_logits.append(logits)

  return np.concatenate(all_logits), np.concatenate(all_labels)


BIRD_CLASSES = list(range(80, 100 + 1))
BICYCLE_CLASSES = [671, 444]


def main():
  BATCH_SIZE = 1
  ### Get data
  data_dir = tcu_images.get_dataset('train')

  train_dataset = torchvision.datasets.ImageFolder(
    data_dir,
    torchvision.transforms.Compose([
      torchvision.transforms.Resize(224),
      torchvision.transforms.ToTensor(),
      lambda x: x / 255.,
    ]))

  dataset_iter = [(x.numpy(), y.numpy())
                  for (x, y) in
                  iter(torch.utils.data.DataLoader(train_dataset, batch_size=BATCH_SIZE))]

  ### Load model
  pytorch_model = torchvision.models.resnet50(pretrained=True)
  pytorch_model = pytorch_model.cuda()
  pytorch_model.eval()  # switch to eval mode

  def model_fn(x_np):
    with torch.no_grad():
      x = torch.from_numpy(x_np).cuda()
      logits1000 = pytorch_model(x)

      # model API needs a single logit. Positive values correspond to bicycle
      bird_max_logit, _ = torch.max(logits1000[:, BIRD_CLASSES], dim=1)
      bicycle_max_logit, _ = torch.max(logits1000[:, BICYCLE_CLASSES], dim=1)
      delta_logits = bicycle_max_logit - bird_max_logit

      return delta_logits.cpu().numpy()

  ### Evaluate attack
  for attack_fn in [null_attack, spsa_attack]:
    logits, labels = run_attack(model_fn, dataset_iter, attack_fn,
                              max_num_batches=1)
    preds = (logits > 0).astype(np.int64)
    correct = np.equal(preds, labels).astype(np.float32)
    correct_fracs = np.sum(correct, axis=0) / len(labels)
    print(correct_fracs)


if __name__ == '__main__':
  main()
