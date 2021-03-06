'''
Created on Nov, 2017

@author: FrancesZhou
'''

from __future__ import absolute_import

import os
import time
import math
import numpy as np
import copy
import tensorflow as tf
from progressbar import *
from sklearn.neighbors import NearestNeighbors
# from biLSTM.preprocessing.preprocessing import batch_data, get_max_seq_len, construct_train_test_corpus, \
#     generate_labels_from_file, generate_label_pair_from_file
# from biLSTM.utils.io_utils import load_pickle, write_file, load_txt
from model.utils.op_utils import *
from model.utils.io_utils import load_pickle, dump_pickle


class ModelSolver(object):
    def __init__(self, model, train_data, test_data, **kwargs):
        self.model = model
        self.train_data = train_data
        self.test_data = test_data
        self.if_use_seq_len = kwargs.pop('if_use_seq_len', 0)
        self.if_output_all_labels = kwargs.pop('if_output_all_labels', 0)
        self.show_batches = kwargs.pop('show_batches', 20)
        self.n_epochs = kwargs.pop('n_epochs', 10)
        self.batch_size = kwargs.pop('batch_size', 32)
        self.batch_pid_size = kwargs.pop('batch_pid_size', 4)
        self.alpha = kwargs.pop('alpha', 0.2)
        self.learning_rate = kwargs.pop('learning_rate', 0.0001)
        self.g_learning_rate = kwargs.pop('learning_rate', 0.0001)
        self.update_rule = kwargs.pop('update_rule', 'adam')
        self.model_path = kwargs.pop('model_path', './model/')
        self.log_path = kwargs.pop('log_path', './log/')
        self.pretrained_model = kwargs.pop('pretrained_model', None)
        self.test_path = kwargs.pop('test_path', None)
        self.use_sne = kwargs.pop('use_sne', 0)
        self.saved_dis = kwargs.pop('saved_dis', 0)
        if not os.path.exists(self.model_path):
            os.makedirs(self.model_path)
        if not os.path.exists(self.log_path):
            os.makedirs(self.log_path)
        if not os.path.exists(self.log_path + '/train'):
            os.makedirs(self.log_path + '/train')
        if not os.path.exists(self.log_path + '/test'):
            os.makedirs(self.log_path + '/test')
        if self.update_rule == 'adam':
            self.optimizer = tf.train.AdamOptimizer
        elif self.update_rule == 'momentum':
            self.optimizer = tf.train.MomentumOptimizer
        elif self.update_rule == 'rmsprop':
            self.optimizer = tf.train.RMSPropOptimizer


    def train(self, output_file_path):
        o_file = open(output_file_path, 'w')
        train_loader = self.train_data
        test_loader = self.test_data
        # build_model
        _, y_, loss, w_1, w_2, y_out = self.model.build_model()
        w1_l = 0
        w2_l = 0
        y_out_vis = 0
        sne_loss = self.model.t_sne()
        # train op
        with tf.name_scope('optimizer'):
            # ========== loss
            optimizer = self.optimizer(learning_rate=self.learning_rate)
            train_op = optimizer.minimize(loss, global_step=tf.train.get_global_step())
            sne_train_op = optimizer.minimize(sne_loss, global_step=tf.train.get_global_step())
        # summay
        # merged = tf.summary.merge_all()
        tf.get_variable_scope().reuse_variables()
        #
        # set upper limit of used gpu memory
        gpu_options = tf.GPUOptions(allow_growth=True)
        with tf.Session(config=tf.ConfigProto(gpu_options=gpu_options)) as sess:
            train_writer = tf.summary.FileWriter(self.log_path + '/train', sess.graph)
            test_writer = tf.summary.FileWriter(self.log_path + '/test')
            tf.global_variables_initializer().run()
            saver = tf.train.Saver(tf.global_variables())
            if self.pretrained_model is not None:
                print 'Start training with pretrained model...'
                pretrained_model_path = self.model_path + self.pretrained_model
                saver.restore(sess, pretrained_model_path)
            # ============== begin training ===================
            if self.use_sne:
                if self.saved_dis:
                    train_loader.load_distance_matrix()
                else:
                    train_loader.get_distance_matrix()
            for e in xrange(self.n_epochs):
                print '========== begin epoch %d ===========' % e
                curr_loss = 0
                curr_sne_loss = 0
                val_loss = 0
                # '''
                # ------------- train ----------------
                num_train_points = len(train_loader.train_pids)
                train_pid_batches = xrange(int(math.ceil(num_train_points * 1.0 / self.batch_size)))
                #print 'num of train batches:    %d' % len(train_pid_batches)
                widgets = ['Train: ', Percentage(), ' ', Bar('-'), ' ', ETA()]
                pbar = ProgressBar(widgets=widgets, maxval=len(train_pid_batches)).start()
                for i in train_pid_batches:
                    pbar.update(i)
                    _, x_feature_id, x_feature_v, y = train_loader.get_pid_x(train_loader.train_pids,
                                                                                    i*self.batch_size, (i+1)*self.batch_size)
                    x_feature_v = x_feature_v/np.linalg.norm(x_feature_v, 2, axis=-1, keepdims=True)
                    #x_feature_v += np.random.normal(0, 0.0001, x_feature_v.shape)
                    if len(y) == 0:
                        continue
                    feed_dict = {self.model.x_feature_id: np.array(x_feature_id, dtype=np.int32),
                                 self.model.x_feature_v: np.array(x_feature_v, dtype=np.float32),
                                 self.model.y: np.array(y, dtype=np.float32)
                                 }
                    _, l_, w1_l, w2_l, y_out_vis = sess.run([train_op, loss, w_1, w_2, y_out], feed_dict)
                    #if i == len(train_pid_batches)-1:
                    #    train_summary = sess.run(merged, feed_dict)
                    #    train_writer.add_summary(train_summary, e)
                    curr_loss += l_
                pbar.finish()
                # ---- sne regularization ----
                num_sne_points = 100
                if self.use_sne:
                    sne_pids = train_loader.pid_dis_keys
                    np.random.shuffle(sne_pids)
                    num_sne_points = len(sne_pids)
                    sne_pids_batch = xrange(num_sne_points)
                    widgets = ['Train_sne: ', Percentage(), ' ', Bar('-'), ' ', ETA()]
                    pbar = ProgressBar(widgets=widgets, maxval=num_sne_points).start()
                    for i in sne_pids_batch:
                        pbar.update(i)
                        p1_f_id, p1_f_v, p2_f_id, p2_f_v, p2_dis = train_loader.get_pid_pid_dis(sne_pids[i])
                        #print p1_f_id
                        p1_f_v = p1_f_v/np.linalg.norm(p1_f_v, 2, axis=1, keepdims=True)
                        p2_f_v = p2_f_v/np.linalg.norm(p2_f_v, 2, axis=1, keepdims=True)
                        feed_dict = {self.model.p1_f_id: np.array(p1_f_id, dtype=np.int32),
                                     self.model.p1_f_v: p1_f_v,
                                     self.model.p2_f_id: p2_f_id,
                                     self.model.p2_f_v: p2_f_v,
                                     self.model.p1_p2_dis: p2_dis}
                        _, sne_l_ = sess.run([sne_train_op, sne_loss], feed_dict)
                        curr_sne_loss += sne_l_
                        # if i == len(sne_pids_batch)-1:
                        #     train_summary = sess.run(merged, feed_dict)
                        #     train_writer.add_summary(train_summary, e*2 + 1)
                    pbar.finish()
                # -------------- validate -------------
                num_val_points = len(train_loader.val_pids)
                val_pid_batches = xrange(int(math.ceil(num_val_points*1.0 / self.batch_size)))
                #print 'num of validate pid batches: %d' % len(val_pid_batches)
                pre_pid_label_prop = {}
                tar_pid_label_prop = {}
                pre_pid_label = {}
                tar_pid_label = {}
                widgets = ['Validate: ', Percentage(), ' ', Bar('-'), ' ', ETA()]
                pbar = ProgressBar(widgets=widgets, maxval=len(val_pid_batches)).start()
                for i in val_pid_batches:
                    pbar.update(i)
                    batch_pid, x_feature_id, x_feature_v, y = train_loader.get_pid_x(train_loader.val_pids,
                                                                                            i*self.batch_size, (i+1)*self.batch_size)
                    x_feature_v = x_feature_v / np.linalg.norm(x_feature_v, 2, axis=-1, keepdims=True)
                    feed_dict = {self.model.x_feature_id: np.array(x_feature_id, dtype=np.int32),
                                 self.model.x_feature_v: np.array(x_feature_v, dtype=np.float32),
                                 self.model.y: np.array(y)
                                 }
                    y_p, l_ = sess.run([y_, loss], feed_dict)
                    val_loss += l_
                    # prediction
                    for p_i in xrange(len(batch_pid)):
                        pid = batch_pid[p_i]
                        pre_label_index = np.argsort(-np.array(y_p[p_i]))[:5]
                        pre_pid_label_prop[pid] = [y[p_i][ind]*(train_loader.label_prop[ind]) for ind in pre_label_index]
                        tar_pid_label_prop[pid] = [train_loader.label_prop[train_loader.label_dict[q]] for q in train_loader.label_data[pid]]
                        #
                        pre_pid_label[pid] = [y[p_i][ind] for ind in pre_label_index]
                        tar_pid_label[pid] = np.ones_like([train_loader.label_dict[q] for q in train_loader.label_data[pid]])
                pbar.finish()
                val_results = results_for_prop_vector(tar_pid_label, pre_pid_label)
                val_prop_results = results_for_prop_vector(tar_pid_label_prop, pre_pid_label_prop)
                # reset train_loader
                train_loader.reset_data()
                # ====== output loss ======
                w_text = 'at epoch %d, train loss is %f, w_1 is %f, w_2 is %f, y_out_add is %f ' % (e, curr_loss/len(train_pid_batches), w1_l, w2_l, y_out_vis)
                print w_text
                o_file.write(w_text)
                w_text = 'at epoch %d, sne loss is %f ' % (e, curr_sne_loss / num_sne_points)
                print w_text
                o_file.write(w_text)
                w_text = 'at epoch %d, val loss is %f ' % (e, val_loss/len(val_pid_batches))
                print w_text
                o_file.write(w_text)
                w_text = 'at epoch %d, val_results: \n' % e
                w_text = w_text + str(val_results) + '\n'
                #print w_text
                o_file.write(w_text)
                w_text = 'at epoch {0}, val_prop_results: \n'.format(e)
                w_text = w_text + str(val_prop_results) + '\n'
                #print w_text
                o_file.write(w_text)
                # ====== save model ========
                save_name = self.model_path + 'model'
                saver.save(sess, save_name, global_step=e+1)
                print 'model-%s saved.' % (e+1)
                # '''
                # ----------------- test ---------------------
                if e % 1 == 0:
                    print '=============== test ================'
                    test_loss = 0
                    num_test_points = len(test_loader.pids)
                    test_pid_batches = xrange(int(math.ceil(num_test_points * 1.0 / self.batch_size)))
                    #print 'num of test pid batches: %d' % len(test_pid_batches)
                    pre_pid_label_prop = {}
                    tar_pid_label_prop = {}
                    widgets = ['Test: ', Percentage(), ' ', Bar('#'), ' ', ETA()]
                    pbar = ProgressBar(widgets=widgets, maxval=len(test_pid_batches)).start()
                    for i in test_pid_batches:
                        pbar.update(i)
                        batch_pid, x_feature_id, x_feature_v, y = test_loader.get_pid_x(test_loader.pids,
                                                                                                i * self.batch_size, (
                                                                                                i + 1) * self.batch_size)
                        x_feature_v = x_feature_v / np.linalg.norm(x_feature_v, 2, axis=-1, keepdims=True)
                        feed_dict = {self.model.x_feature_id: np.array(x_feature_id, dtype=np.int32),
                                     self.model.x_feature_v: np.array(x_feature_v, dtype=np.float32),
                                     self.model.y: np.array(y)
                                     }
                        y_p, l_ = sess.run([y_, loss], feed_dict)
                        # test_summary = sess.run(merged, feed_dict)
                        # test_writer.add_summary(test_summary, i)
                        test_loss += l_
                        # prediction
                        for p_i in xrange(len(batch_pid)):
                            pid = batch_pid[p_i]
                            pre_label_index = np.argsort(-np.array(y_p[p_i]))[:5]
                            pre_pid_label_prop[pid] = [y[p_i][ind] * (test_loader.label_prop[ind]) for ind in
                                                       pre_label_index]
                            tar_pid_label_prop[pid] = [test_loader.label_prop[test_loader.label_dict[q]] for q in test_loader.label_data[pid]]
                            #
                            pre_pid_label[pid] = [y[p_i][ind] for ind in pre_label_index]
                            tar_pid_label[pid] = np.ones_like([test_loader.label_dict[q] for q in test_loader.label_data[pid]])
                    pbar.finish()
                    test_results = results_for_prop_vector(tar_pid_label, pre_pid_label)
                    test_prop_results = results_for_prop_vector(tar_pid_label_prop, pre_pid_label_prop)
                    w_text = 'at epoch %d, test loss is %f ' % (e, test_loss/len(test_pid_batches))
                    print w_text
                    o_file.write(w_text)
                    w_text = 'at epoch %d, test_results: \n' % e
                    w_text = w_text + str(test_results) + '\n'
                    print w_text
                    o_file.write(w_text)
                    w_text = 'at epoch {0}, test_prop_results: \n'.format(e)
                    w_text = w_text + str(test_prop_results) + '\n'
                    print w_text
                    o_file.write(w_text)
            # save model
            train_writer.close()
            test_writer.close()
            save_name = self.model_path + 'model_final'
            saver.save(sess, save_name)
            print 'final model saved.'
            o_file.close()
